"""Loader for the golden evaluation dataset.

Every downstream eval script should read the corpus through this module rather than re-parsing
the Word documents, so all of them see the same 16 transcripts.

    from lib.golden import load_golden, load_turns, load_jsonl

    df = load_golden()                  # one row per transcript; df.turns is list[{role, text}]
    turns = load_turns()                # exploded, one row per turn
    raw = load_jsonl()                  # the snapshot as plain dicts

"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
GOLDEN_DIR = REPO_ROOT / "datasets" / "golden"
XLSX_PATH = GOLDEN_DIR / "human_scores.xlsx"
JSONL_PATH = GOLDEN_DIR / "transcripts.jsonl"

EXPECTED_ROWS = 16

# Exact DimensionName literals from backend/app/evaluation/iqr_schema.py.
DIMENSIONS = [
    "framing_and_stakeholder_fit",
    "question_quality_and_precision",
    "probing_and_follow_up_depth",
    "listening_interpretation_and_stewardship",
]
DIMENSION_COLUMNS = [f"human_dim_{d}" for d in DIMENSIONS]

TIERS = ["t1", "t2", "t3"]
SIC_FIELDS = ["pct", "found", "total", "status"]
SIC_COLUMNS = [f"human_sic_{t}_{f}" for t in TIERS for f in SIC_FIELDS]

PERSONA_IDS = {
    "alex_martinez",
    "michael_mike_alvarez",
    "sarah_donnelly",
    "thomas_tom_caldwell",
}


class GoldenDatasetError(RuntimeError):
    """Raised when the dataset is missing or fails its integrity checks."""


def _require(path: Path) -> Path:
    if not path.exists():
        raise GoldenDatasetError(
            f"{path.relative_to(REPO_ROOT)} not found -- build it with:\n"
            f"    cd backend && uv run python scripts/build_golden_dataset.py --verify"
        )
    return path


def load_golden(source: str = "xlsx", validate: bool = True) -> pd.DataFrame:
    """Load the normalized frame: one row per transcript.

    `turns` comes back as a real list[{"role", "text"}] rather than the stored JSON string, so
    callers get the nested shape without re-parsing.

    Args:
        source: "xlsx" reads the workbook's scores sheet; "jsonl" reads the snapshot.
        validate: run integrity checks (row count, persona coverage, non-null scores).
    """
    if source == "xlsx":
        frame = pd.read_excel(_require(XLSX_PATH), sheet_name="scores")
        frame["turns"] = frame["turns_json"].apply(json.loads)
    elif source == "jsonl":
        records = load_jsonl()
        flat = []
        for record in records:
            row = {k: v for k, v in record.items() if k not in {"human_dim", "human_sic"}}
            for dimension, value in record["human_dim"].items():
                row[f"human_dim_{dimension}"] = value
            for tier, block in record["human_sic"].items():
                for field, value in block.items():
                    row[f"human_sic_{tier}_{field}"] = value
            row["turn_count"] = len(record["turns"])
            flat.append(row)
        frame = pd.DataFrame(flat)
    else:
        raise ValueError(f"source must be 'xlsx' or 'jsonl', got {source!r}")

    frame = frame.sort_values("transcript_id").reset_index(drop=True)
    if validate:
        _validate(frame)
    return frame


def load_turns(source: str = "xlsx") -> pd.DataFrame:
    """Load the exploded frame: one row per turn.

    Columns: transcript_id, turn_index, speaker, role, text.
    """
    if source == "xlsx":
        turns = pd.read_excel(_require(XLSX_PATH), sheet_name="turns")
    elif source == "jsonl":
        rows = [
            {"transcript_id": record["transcript_id"], **turn}
            for record in load_jsonl()
            for turn in record["turns"]
        ]
        turns = pd.DataFrame(rows)
    else:
        raise ValueError(f"source must be 'xlsx' or 'jsonl', got {source!r}")
    return turns.sort_values(["transcript_id", "turn_index"]).reset_index(drop=True)


def load_jsonl(path: Path | str | None = None) -> list[dict]:
    """Load the raw snapshot as a list of dicts, one per transcript."""
    target = _require(Path(path) if path is not None else JSONL_PATH)
    with target.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _validate(frame: pd.DataFrame) -> None:
    problems: list[str] = []

    if len(frame) != EXPECTED_ROWS:
        problems.append(f"expected {EXPECTED_ROWS} rows, got {len(frame)}")
    if frame["transcript_id"].duplicated().any():
        problems.append("duplicate transcript_id")

    personas = set(frame["persona_id"])
    if personas != PERSONA_IDS:
        problems.append(f"persona coverage {sorted(personas)} != {sorted(PERSONA_IDS)}")

    for column in ["human_overall", *DIMENSION_COLUMNS, *SIC_COLUMNS]:
        if column not in frame.columns:
            problems.append(f"missing column {column}")
        elif frame[column].isna().any():
            missing = frame.loc[frame[column].isna(), "transcript_id"].tolist()
            problems.append(f"{column} is null for {missing}")

    for _, row in frame.iterrows():
        turns = row["turns"]
        if not isinstance(turns, list) or not turns:
            problems.append(f"{row['transcript_id']}: turns is not a non-empty list")
            continue
        if len(turns) != row["turn_count"]:
            problems.append(f"{row['transcript_id']}: turn_count {row['turn_count']} != {len(turns)}")
        for turn in turns:
            if turn.get("role") not in {"user", "assistant"}:
                problems.append(f"{row['transcript_id']}: bad role {turn.get('role')!r}")
                break
            if not str(turn.get("text", "")).strip():
                problems.append(f"{row['transcript_id']}: empty turn text")
                break

    if problems:
        raise GoldenDatasetError("golden dataset failed validation:\n  - " + "\n  - ".join(problems))


if __name__ == "__main__":
    df = load_golden()
    print(f"{len(df)} transcripts, {df['turn_count'].sum()} turns")
    print(
        df[
            [
                "transcript_id",
                "persona_id",
                "quality",
                "prompt_era",
                "turn_count",
                "human_overall",
                "human_skill_label",
            ]
        ].to_string(index=False)
    )
