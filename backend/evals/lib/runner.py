"""Async runner: bounded concurrency, retry, budget guard, append-only output.

Every study calls this. Collection writes one JSONL record per call and a
manifest describing the run; analysis reads those files and never touches the
network. That split is what makes a statistic re-derivable without re-spending,
and what makes an interrupted run resumable.
"""

from __future__ import annotations

import asyncio
import json
import os
import platform
import random
import secrets
import socket
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from evals.lib.cache import HARNESS_VERSION, CallCache

EVALS_ROOT = Path(__file__).resolve().parents[1]
RUNS_DIR = EVALS_ROOT / "runs"
CACHE_DIR = EVALS_ROOT / ".cache"

# Errors worth a second attempt: transport hiccups and server-side throttling.
# A schema violation or a bad request is deterministic — retrying it just burns
# the budget more slowly.
_RETRYABLE = (
    "rate limit", "ratelimit", "429", "500", "502", "503", "504",
    "timeout", "timed out", "connection", "apiconnection", "overloaded",
)


def _is_retryable(error: str) -> bool:
    low = error.lower()
    return any(token in low for token in _RETRYABLE)


@dataclass
class CallSpec:
    """One unit of work. `key` is the cache identity; `meta` is written to the
    JSONL row so analysis never has to re-derive what a record describes."""

    kind: str
    key: str
    meta: dict = field(default_factory=dict)


@dataclass
class RunConfig:
    name: str
    model: str
    concurrency: int = 8
    max_usd: float = 20.0
    max_attempts: int = 4
    temperature: float = 0.0


class BudgetExceeded(RuntimeError):
    pass


def _git_state() -> dict:
    def _run(*args: str) -> str:
        try:
            return subprocess.run(
                args, capture_output=True, text=True, timeout=10, cwd=EVALS_ROOT
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""

    sha = _run("git", "rev-parse", "HEAD")
    return {
        "git_sha": sha or "unknown",
        # A number measured from uncommitted code cannot be reproduced from the
        # sha alone, so the run says so rather than implying otherwise.
        "git_dirty": bool(_run("git", "status", "--porcelain")),
    }


class Runner:
    def __init__(self, config: RunConfig, cache_dir: Optional[Path] = None) -> None:
        self.config = config
        self.cache = CallCache(cache_dir or CACHE_DIR)
        self.spent_usd = 0.0
        self.api_calls = 0
        self._lock = asyncio.Lock()
        self._aborted = False

        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        # The random suffix is not decoration: two runs of the same study started
        # within one second would otherwise share a directory, append to one
        # another's records.jsonl, and overwrite each other's manifest.
        self.run_id = f"{stamp}_{config.name}_{config.model}_{secrets.token_hex(3)}"
        self.run_dir = RUNS_DIR / config.name / self.run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.jsonl_path = self.run_dir / "records.jsonl"
        self._jsonl = self.jsonl_path.open("a", encoding="utf-8")

    # ── recording ────────────────────────────────────────────────────────────

    def _write(self, record: dict) -> None:
        self._jsonl.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        self._jsonl.flush()

    async def _charge(self, usd: float) -> None:
        async with self._lock:
            self.spent_usd += usd
            self.api_calls += 1

    async def _check_budget(self) -> None:
        async with self._lock:
            if self.spent_usd >= self.config.max_usd:
                self._aborted = True
                raise BudgetExceeded(
                    f"spent ${self.spent_usd:.2f} of ${self.config.max_usd:.2f} ceiling"
                )

    # ── execution ────────────────────────────────────────────────────────────

    async def _execute_one(
        self,
        spec: CallSpec,
        executor: Callable[[CallSpec], Awaitable[dict]],
        semaphore: asyncio.Semaphore,
        progress: Optional[Callable[[], None]] = None,
    ) -> dict:
        cached = self.cache.get(spec.key)
        if cached is not None:
            record = {**cached, "from_cache": True}
            self._write(record)
            if progress:
                progress()
            return record

        async with semaphore:
            await self._check_budget()
            record: dict = {}
            for attempt in range(1, self.config.max_attempts + 1):
                record = await executor(spec)
                await self._charge(float(record.get("cost_usd") or 0.0))
                error = record.get("error")
                if not error or not _is_retryable(str(error)) or attempt == self.config.max_attempts:
                    record["attempts"] = attempt
                    break
                # Full jitter: a fixed backoff would resynchronise every worker
                # that got throttled at the same moment.
                await asyncio.sleep(random.uniform(0, min(2 ** attempt, 30)))

        record = {**spec.meta, "kind": spec.kind, "key": spec.key, "from_cache": False, **record}
        # Only successful calls are cached. Caching a failure would make the
        # retry-free re-run silently inherit it.
        if not record.get("error"):
            self.cache.put(spec.key, record)
        self._write(record)
        if progress:
            progress()
        return record

    async def run(
        self,
        specs: list[CallSpec],
        executor: Callable[[CallSpec], Awaitable[dict]],
        show_progress: bool = True,
    ) -> list[dict]:
        semaphore = asyncio.Semaphore(self.config.concurrency)
        started = time.time()

        bar = None
        if show_progress:
            try:
                from tqdm import tqdm

                bar = tqdm(total=len(specs), desc=self.config.name, unit="call")
            except ImportError:
                bar = None

        def tick() -> None:
            if bar:
                bar.update(1)
                bar.set_postfix(spent=f"${self.spent_usd:.2f}", hits=self.cache.hits)

        try:
            results = await asyncio.gather(
                *(self._execute_one(s, executor, semaphore, tick) for s in specs),
                return_exceptions=True,
            )
        finally:
            if bar:
                bar.close()

        records, failures = [], []
        for spec, r in zip(specs, results):
            if isinstance(r, BaseException):
                failures.append({"key": spec.key, "error": f"{type(r).__name__}: {r}"})
            else:
                records.append(r)

        self.write_manifest(
            n_specs=len(specs),
            n_records=len(records),
            failures=failures,
            wall_seconds=round(time.time() - started, 1),
        )
        self._jsonl.close()
        return records

    # ── provenance ───────────────────────────────────────────────────────────

    def write_manifest(self, **extra: Any) -> Path:
        manifest = {
            "run_id": self.run_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "config": asdict(self.config),
            "harness_version": HARNESS_VERSION,
            **_git_state(),
            "host": socket.gethostname(),
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "database_url_host": os.getenv("DATABASE_URL", "").split("@")[-1][:60],
            "cache": self.cache.stats,
            "api_calls": self.api_calls,
            "spent_usd": round(self.spent_usd, 4),
            "aborted_on_budget": self._aborted,
            **extra,
        }
        path = self.run_dir / "manifest.json"
        path.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
        return path
