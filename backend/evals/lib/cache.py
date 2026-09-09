"""Content-addressed cache for LLM calls.

Collection is the only part of the suite that costs money, so it is separated
from analysis by this cache: re-running a study with unchanged inputs makes zero
API calls, and an interrupted run resumes by replaying the hits and issuing only
the misses.

The key hashes file *contents*, not version directory names. Hashing "v2" would
mean an in-place edit to prompts/iqr/v2/system_prompt.txt kept serving results
from before the edit, forever, with nothing looking wrong.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Optional

# Bump when a change to the harness invalidates previously cached records —
# a different call shape, or a fix that changes what gets stored. Cosmetic
# changes here should NOT bump it, or every study pays to re-run.
HARNESS_VERSION = "1"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def sha256_obj(obj: Any) -> str:
    """Stable hash of any JSON-serialisable object."""
    return sha256_text(json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str))


def call_key(**parts: Any) -> str:
    """Identity of one LLM call.

    Callers pass: scorer, model, temperature, prompt_sha, rubric_sha, schema_sha,
    transcript_sha, persona_id, run_idx. harness_version is added here so no
    caller can forget it.
    """
    parts.setdefault("harness_version", HARNESS_VERSION)
    return sha256_obj(parts)


class CallCache:
    """JSON records on disk, sharded two levels deep by key prefix.

    A flat directory would hold ~1,000 files for B1 alone and several thousand
    once B4's relevance judgements land, which makes every `ls` slow and every
    listing useless.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.hits = 0
        self.misses = 0

    def _path(self, key: str) -> Path:
        return self.root / key[:2] / f"{key}.json"

    def get(self, key: str) -> Optional[dict]:
        path = self._path(key)
        if not path.is_file():
            self.misses += 1
            return None
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # A truncated record (interrupted write, full disk) is a miss, not a
            # crash — the call is simply made again and the file overwritten.
            self.misses += 1
            return None
        self.hits += 1
        return record

    def put(self, key: str, record: dict) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write-then-rename so an interrupted run cannot leave a half-written
        # record that a later run would read as a hit.
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(record, ensure_ascii=False, default=str), encoding="utf-8")
        tmp.replace(path)

    @property
    def stats(self) -> dict:
        return {"hits": self.hits, "misses": self.misses}
