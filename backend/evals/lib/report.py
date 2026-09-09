"""Report emitters.

Every study writes a dated Markdown file with the same header, so a number can
always be traced back to the code, prompts and data that produced it. A figure
without that header is not reportable — six months on, nobody can tell whether
it came from prompt v2 or v3, or from gpt-4o or a silent fallback to mini.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Optional, Sequence

import pandas as pd

EVALS_ROOT = Path(__file__).resolve().parents[1]
REPORTS_DIR = EVALS_ROOT / "reports"


def load_records(run_dir: Path) -> pd.DataFrame:
    """Read a run's JSONL into a frame."""
    path = Path(run_dir) / "records.jsonl"
    with path.open(encoding="utf-8") as handle:
        return pd.DataFrame([json.loads(line) for line in handle if line.strip()])


def latest_run(name: str, model: Optional[str] = None) -> Path:
    """Most recent run directory for a study, optionally filtered by model."""
    root = EVALS_ROOT / "runs" / name
    candidates = sorted(d for d in root.iterdir() if d.is_dir() and (d / "records.jsonl").exists())
    if model:
        candidates = [d for d in candidates if f"_{model}_" in d.name]
    if not candidates:
        raise FileNotFoundError(f"no runs with records under {root}" + (f" for model {model}" if model else ""))
    return candidates[-1]


def read_manifest(run_dir: Path) -> dict:
    path = Path(run_dir) / "manifest.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def md_table(frame: pd.DataFrame, floatfmt: str = "{:.3f}") -> str:
    """Markdown table with numbers formatted consistently."""
    display = frame.copy()
    for col in display.columns:
        if pd.api.types.is_float_dtype(display[col]):
            display[col] = display[col].map(lambda v: "" if pd.isna(v) else floatfmt.format(v))
    header = "| " + " | ".join(str(c) for c in display.columns) + " |"
    rule = "| " + " | ".join("---" for _ in display.columns) + " |"
    rows = ["| " + " | ".join(str(v) for v in row) + " |" for row in display.itertuples(index=False)]
    return "\n".join([header, rule, *rows])


def header_block(title: str, manifest: dict, extra: Optional[dict] = None) -> str:
    config = manifest.get("config", {})
    fields = {
        "date": date.today().isoformat(),
        "git_sha": manifest.get("git_sha", "unknown")[:12]
        + (" (dirty)" if manifest.get("git_dirty") else ""),
        "run_id": manifest.get("run_id", "unknown"),
        "model": config.get("model", "unknown"),
        "temperature": config.get("temperature"),
        "harness": manifest.get("harness_version"),
        "api_calls": manifest.get("api_calls"),
        "spend_usd": manifest.get("spent_usd"),
        "wall_seconds": manifest.get("wall_seconds"),
        **(extra or {}),
    }
    lines = [f"# {title}", "", "| field | value |", "| --- | --- |"]
    lines += [f"| {k} | {v} |" for k, v in fields.items() if v is not None]
    return "\n".join(lines)


def write_report(name: str, body: str, tables: Optional[dict[str, pd.DataFrame]] = None) -> Path:
    """Write <date>_<name>.md plus one CSV per table, and return the Markdown path."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    stem = f"{date.today().isoformat()}_{name}"
    md_path = REPORTS_DIR / f"{stem}.md"
    md_path.write_text(body.rstrip() + "\n", encoding="utf-8")
    for table_name, frame in (tables or {}).items():
        frame.to_csv(REPORTS_DIR / f"{stem}_{table_name}.csv", index=False)
    return md_path


def section(title: str, *parts: Any) -> str:
    chunks: list[str] = [f"## {title}", ""]
    for part in parts:
        if isinstance(part, pd.DataFrame):
            chunks.append(md_table(part))
        elif isinstance(part, Sequence) and not isinstance(part, str):
            chunks.extend(str(p) for p in part)
        else:
            chunks.append(str(part))
        chunks.append("")
    return "\n".join(chunks)
