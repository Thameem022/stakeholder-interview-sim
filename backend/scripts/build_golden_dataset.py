"""Build the golden evaluation dataset from the Trial Run Word documents.

Reads the 16 transcript .docx files under docs/Trial Run/Round {1,2}/Interviews/, joins them
against the grader scores in datasets/golden/raw/diagnostic_scores.yaml, and emits:

    datasets/golden/human_scores.xlsx   sheets: scores | turns | README
    datasets/golden/transcripts.jsonl   canonical snapshot for downstream eval scripts

Run from the backend/ directory:

    uv run python scripts/build_golden_dataset.py --verify

The diagnostic .docx reports hold page images rather than text, so their numbers cannot be parsed
here; they live in the YAML instead. See that file's header for the transcription caveats.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import docx
import pandas as pd
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
TRIAL_RUN = REPO_ROOT / "docs" / "Trial Run"
GOLDEN_DIR = REPO_ROOT / "datasets" / "golden"
SCORES_YAML = GOLDEN_DIR / "raw" / "diagnostic_scores.yaml"
TARGETS_YAML = GOLDEN_DIR / "raw" / "calibration_targets.yaml"
XLSX_OUT = GOLDEN_DIR / "human_scores.xlsx"
JSONL_OUT = GOLDEN_DIR / "transcripts.jsonl"

# Folder label -> (interview date, memo round number, prompt era).
# The folder names disagree with the memos they contain: folder "Round 1" holds the June 15 memo
# titled "Round 3", folder "Round 2" holds the June 8 memo titled "Round 2". Both raw labels are
# preserved as columns; transcript_id is keyed by date so the mismatch cannot mislead.
ROUNDS = {
    "Round 2": {"date": "2026-06-06", "memo_round": 2, "prompt_era": "v1_pre_calibration"},
    "Round 1": {"date": "2026-06-15", "memo_round": 3, "prompt_era": "v1_post_calibration"},
}

# Speaker label as rendered in the transcripts -> canonical persona_id.
# The docs drop the quotes around nicknames, matching what convert_transcript_to_iqr derives
# via persona_key.replace("_", " ").title().
SPEAKER_TO_PERSONA = {
    "Alex Martinez": ("alex_martinez", "Alex Martinez"),
    "Michael Mike Alvarez": ("michael_mike_alvarez", 'Michael "Mike" Alvarez'),
    "Sarah Donnelly": ("sarah_donnelly", "Sarah Donnelly"),
    "Thomas Tom Caldwell": ("thomas_tom_caldwell", 'Thomas "Tom" Caldwell'),
}

STUDENT_SPEAKER = "Student"

# Exact DimensionName literals from backend/app/evaluation/iqr_schema.py, so the columns join
# against live scorer output with no renaming.
DIMENSIONS = [
    "framing_and_stakeholder_fit",
    "question_quality_and_precision",
    "probing_and_follow_up_depth",
    "listening_interpretation_and_stewardship",
]

TIERS = ["t1", "t2", "t3"]
SIC_FIELDS = ["pct", "found", "total", "status"]

PREAMBLE = {"Conversation transcript", "Full dialogue from the selected interview."}
TURN_HEADER = re.compile(r"^Turn\s*(\d+)\s*[—–-]\s*(.+)$")

# Every transcript in this set predates ceef62d (2026-08-13) and 51f2e82 "Implemented V2 prompt"
# (2026-07-05), so none of these scores is reproducible against the current scorer.
PREDATES_CEEF62D = True


def parse_transcript(path: Path) -> tuple[list[dict], str]:
    """Return (turns, persona speaker label) for one transcript .docx."""
    document = docx.Document(str(path))
    paragraphs = [p.text.replace("\xa0", " ").strip() for p in document.paragraphs]
    paragraphs = [p for p in paragraphs if p and p not in PREAMBLE]

    turns: list[dict] = []
    persona_speaker: str | None = None
    pending_speaker: str | None = None

    for text in paragraphs:
        header = TURN_HEADER.match(text)
        if header:
            pending_speaker = header.group(2).strip()
            if pending_speaker != STUDENT_SPEAKER:
                persona_speaker = pending_speaker
            continue
        if pending_speaker is None:
            raise ValueError(f"{path.name}: body paragraph before any turn header: {text[:60]!r}")
        turns.append(
            {
                "turn_index": len(turns) + 1,
                "speaker": pending_speaker,
                # Turn.role in backend/app/realtime/session.py is "user" | "assistant".
                "role": "user" if pending_speaker == STUDENT_SPEAKER else "assistant",
                "text": text,
            }
        )
        pending_speaker = None

    if persona_speaker is None:
        raise ValueError(f"{path.name}: no stakeholder turns found")
    return turns, persona_speaker


def discover() -> list[dict]:
    """Walk the Trial Run folders and parse every transcript."""
    records: list[dict] = []
    for folder, meta in ROUNDS.items():
        interviews = TRIAL_RUN / folder / "Interviews"
        if not interviews.is_dir():
            raise FileNotFoundError(f"missing {interviews}")
        for path in sorted(interviews.glob("*_Transcript_*.docx")):
            if path.name.startswith("~$"):  # Word lock file
                continue
            stem = path.name.split("_Transcript_")[0]  # "Alex Good" | "Alex_Bad"
            slug = re.sub(r"[ _]+", "_", stem).lower()  # alex_good | alex_bad
            quality = "good" if slug.endswith("_good") else "bad"
            turns, persona_speaker = parse_transcript(path)
            if persona_speaker not in SPEAKER_TO_PERSONA:
                raise ValueError(f"{path.name}: unknown stakeholder speaker {persona_speaker!r}")
            persona_id, persona_label = SPEAKER_TO_PERSONA[persona_speaker]
            diagnostic = path.name.replace("_Transcript_", "_Interview Diagnostic_")
            records.append(
                {
                    "transcript_id": f"{meta['date']}_{slug}",
                    "persona_id": persona_id,
                    "persona_label": persona_label,
                    "quality": quality,
                    "interview_date": meta["date"],
                    "source_folder": folder,
                    "memo_round": meta["memo_round"],
                    "prompt_era": meta["prompt_era"],
                    "predates_ceef62d": PREDATES_CEEF62D,
                    "turns": turns,
                    "source_transcript_docx": f"{folder}/Interviews/{path.name}",
                    "source_diagnostic_docx": f"{folder}/Interviews/{diagnostic}",
                }
            )
    return records


def attach_scores(records: list[dict]) -> None:
    """Join the grader scores onto the parsed transcripts, in place."""
    scores = yaml.safe_load(SCORES_YAML.read_text())
    found = {r["transcript_id"] for r in records}
    missing = found - set(scores)
    extra = set(scores) - found
    if missing:
        raise SystemExit(f"no score block for: {sorted(missing)}")
    if extra:
        raise SystemExit(f"score block with no transcript: {sorted(extra)}")

    for record in records:
        block = scores[record["transcript_id"]]
        record["human_overall"] = float(block["overall"])
        record["human_skill_label"] = block["skill_label"]
        for dimension in DIMENSIONS:
            record[f"human_dim_{dimension}"] = float(block["dimensions"][dimension])
        for tier in TIERS:
            tier_block = block["sic"][tier]
            for field in SIC_FIELDS:
                value = tier_block[field]
                record[f"human_sic_{tier}_{field}"] = value


def build_targets(records: list[dict]) -> pd.DataFrame:
    """Build the calibration_targets sheet: what the grader's memos say the scores should be.

    These are DERIVED from memo prose, not observed. Every row carries the quote it came from
    and a `basis` saying how well-supported the number is. Rows the memos are silent about get
    target == observed so the sheet stays a complete 16-row join key.
    """
    targets = yaml.safe_load(TARGETS_YAML.read_text())
    observed = {r["transcript_id"]: r for r in records}

    missing = set(observed) - set(targets)
    extra = set(targets) - set(observed)
    if missing:
        raise SystemExit(f"no calibration target block for: {sorted(missing)}")
    if extra:
        raise SystemExit(f"calibration target with no transcript: {sorted(extra)}")

    rows = []
    for transcript_id in sorted(observed):
        block = targets[transcript_id]
        record = observed[transcript_id]
        row = {
            "transcript_id": transcript_id,
            "persona_id": record["persona_id"],
            "quality": record["quality"],
            "interview_date": record["interview_date"],
            "basis": block["basis"],
            "observed_overall": record["human_overall"],
            "target_overall": block.get("target_overall", record["human_overall"]),
        }
        row["overall_delta"] = round(row["target_overall"] - row["observed_overall"], 2)
        for tier in TIERS:
            for field in ("pct", "found"):
                key = f"sic_{tier}_{field}"
                row[f"observed_{key}"] = record[f"human_{key}"]
                row[f"target_{key}"] = block.get(f"target_{key}", record[f"human_{key}"])
            row[f"observed_sic_{tier}_status"] = record[f"human_sic_{tier}_status"]
            row[f"target_sic_{tier}_status"] = block.get(
                f"target_sic_{tier}_status", record[f"human_sic_{tier}_status"]
            )
        row["changed"] = bool(
            row["overall_delta"]
            or any(row[f"observed_sic_{t}_{f}"] != row[f"target_sic_{t}_{f}"] for t in TIERS for f in ("pct", "found"))
        )
        row["memo"] = block.get("memo", "")
        row["memo_quote"] = " ".join(block.get("quote", "").split())
        row["rationale"] = " ".join(block.get("rationale", "").split())
        row["overall_unchanged_because"] = " ".join(block.get("overall_unchanged_because", "").split())
        rows.append(row)
    return pd.DataFrame(rows)


README_ROWS = [
    ("What this workbook is", "Golden evaluation dataset for the Stakeholder Engagement Simulator, built from the 16 pilot interviews under docs/Trial Run/."),
    ("Generated by", "backend/scripts/build_golden_dataset.py -- regenerate rather than editing by hand."),
    ("", ""),
    ("Score source", "Human grader scores for each interview, reviewed by the grader and recorded in the paired *_Interview Diagnostic_*.docx report. Transcribed into datasets/golden/raw/diagnostic_scores.yaml."),
    ("Treat these as", "The reference scores for this corpus -- the human baseline that model output is compared against."),
    ("Grader's written review", "The two SES Test Memos carry the grader's round-by-round assessment in prose; they contain no per-interview numbers, so the memos and this workbook complement each other."),
    ("", ""),
    ("calibration_targets sheet", "SEPARATE from the scores sheet, and derived rather than observed: what the memos say the scores SHOULD have been. Nobody recorded these numbers -- they are read out of the memo prose. Use them as tuning targets, never as ground truth."),
    ("Why they are kept apart", "The scores sheet is the baseline a scorer is measured against. Mixing derived numbers into it would corrupt every comparison with no way for a reader to tell. Join the two sheets on transcript_id when you want both."),
    ("basis column", "memo_explicit = the memo states a number or band (trust it). memo_directional = the memo gives a direction but no magnitude; the direction is the grader's, the magnitude is interpolated and is the weak part. memo_endorsed = the memo affirms the score as-is. no_guidance = the memo is silent."),
    ("How much is actually stated", "Of 16 interviews, 1 has an explicit numeric band (Tom's June 6 good interview), 5 are directional, 8 are endorsed as-is, 2 have no guidance. Every row carries its memo quote so you can re-judge the reading."),
    ("A caution on the June 15 memo", "It says the engine 'consistently capped the score around an 8.2' -- but that round's good interviews are 8.8/9.0/9.0/8.8. 8.2 was the June 6 ceiling, so read that line as the grader recalling the earlier round. It also explicitly declines a change: 'We don't necessarily need to change the grading math right now.'"),
    ("", ""),
    ("Round labels are misleading", "The folder names disagree with the memos inside them."),
    ("Folder 'Round 1'", "Interviews dated 6.15.2026; contains the June 15 memo titled 'Review of Interview Simulation (Round 3)'. memo_round = 3."),
    ("Folder 'Round 2'", "Interviews dated 6.6.2026; contains the June 8 memo titled 'Review of Interview Simulation (Round 2)'. memo_round = 2."),
    ("Consequence", "Folder 'Round 2' is chronologically EARLIER than folder 'Round 1'. transcript_id is keyed by date to avoid the trap; source_folder and memo_round preserve both raw labels."),
    ("", ""),
    ("The two rounds are not interchangeable", "The interviews were run against different builds of the simulator. All 16 predate commit ceef62d (2026-08-13, 'Reduced the Persona gaurd of opening up') and 51f2e82 (2026-07-05, 'Implemented V2 prompt')."),
    ("How the rounds split", "The 06-06 set also predates the 2026-06-13 calibration commits (858d7ff, 42c031e), so prompt_era separates them: v1_pre_calibration vs v1_post_calibration."),
    ("Visible in the reports", "The 06-06 reports use deficit phrasing ('MISSED' / 'What was missed'); the 06-15 reports use extension phrasing ('EXTEND' / 'Go further') -- the change Round 2's memo recommended."),
    ("How to use it", "Group or filter on prompt_era rather than averaging across both rounds; the personas behaved differently between them."),
    ("", ""),
    ("Dimension columns", "Named with the exact DimensionName literals from backend/app/evaluation/iqr_schema.py, so they join against live scorer output without renaming."),
    ("IQR scale", "1.0-10.0 (matches the ge=1.0, le=10.0 bounds on DimensionAssessment.score)."),
    ("SIC granularity", "Tier level only: percentage, cues found, cues total, status. Per-cue heatmap states were deliberately not extracted -- mapping squares to chunk_ids is positional and unverifiable."),
    ("SIC status values", "full | partial | not_accessed_insufficient_framing | not_accessed_appropriate_restraint (mirrors backend/app/evaluation/sic_scorer.py)."),
    ("Tier 3 percentages", "Credit-weighted, not raw counts (explicit=1.0, indirect=0.7, reflective_silence=0.5), so 4/4 cues can read 77.5%."),
    ("", ""),
    ("turns sheet", "One row per conversation turn. role is 'user' (Student) or 'assistant' (stakeholder), matching the Turn dataclass in backend/app/realtime/session.py."),
    ("Consecutive same-speaker turns", "Preserved as-is. They are real artifacts (the phantom-'bye' bug the memos describe); sanitize_transcript in backend/app/api/eval.py is the layer that filters them at scoring time."),
    ("turns_json", "The same turns embedded as a JSON string on the scores sheet, so one row is self-contained."),
]


def write_workbook(records: list[dict]) -> None:
    score_columns = (
        [
            "transcript_id",
            "persona_id",
            "persona_label",
            "quality",
            "interview_date",
            "source_folder",
            "memo_round",
            "prompt_era",
            "predates_ceef62d",
            "turn_count",
            "turns_json",
            "human_overall",
            "human_skill_label",
        ]
        + [f"human_dim_{d}" for d in DIMENSIONS]
        + [f"human_sic_{t}_{f}" for t in TIERS for f in SIC_FIELDS]
        + ["source_transcript_docx", "source_diagnostic_docx"]
    )

    score_rows = []
    turn_rows = []
    for record in sorted(records, key=lambda r: r["transcript_id"]):
        row = dict(record)
        row["turn_count"] = len(record["turns"])
        row["turns_json"] = json.dumps(
            [{"role": t["role"], "text": t["text"]} for t in record["turns"]],
            ensure_ascii=False,
        )
        score_rows.append({column: row.get(column) for column in score_columns})
        for turn in record["turns"]:
            turn_rows.append({"transcript_id": record["transcript_id"], **turn})

    scores_df = pd.DataFrame(score_rows, columns=score_columns)
    turns_df = pd.DataFrame(turn_rows, columns=["transcript_id", "turn_index", "speaker", "role", "text"])
    targets_df = build_targets(records)
    readme_df = pd.DataFrame(README_ROWS, columns=["Field", "Notes"])

    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(XLSX_OUT, engine="openpyxl") as writer:
        scores_df.to_excel(writer, sheet_name="scores", index=False)
        turns_df.to_excel(writer, sheet_name="turns", index=False)
        targets_df.to_excel(writer, sheet_name="calibration_targets", index=False)
        readme_df.to_excel(writer, sheet_name="README", index=False)
        _autosize(writer.sheets["scores"], scores_df, cap=42)
        _autosize(writer.sheets["turns"], turns_df, cap=110)
        _autosize(writer.sheets["calibration_targets"], targets_df, cap=80)
        _autosize(writer.sheets["README"], readme_df, cap=130)

    changed = int(targets_df["changed"].sum())
    print(
        f"wrote {XLSX_OUT.relative_to(REPO_ROOT)}  "
        f"({len(scores_df)} scores, {len(turns_df)} turns, {len(targets_df)} targets / {changed} adjusted)"
    )


def _autosize(sheet, frame: pd.DataFrame, cap: int) -> None:
    from openpyxl.styles import Alignment
    from openpyxl.utils import get_column_letter

    sheet.freeze_panes = "A2"
    for index, column in enumerate(frame.columns, start=1):
        longest = max([len(str(column))] + [len(str(v)) for v in frame[column].head(200)])
        sheet.column_dimensions[get_column_letter(index)].width = min(max(longest + 2, 10), cap)
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)


def write_jsonl(records: list[dict]) -> None:
    with JSONL_OUT.open("w", encoding="utf-8") as handle:
        for record in sorted(records, key=lambda r: r["transcript_id"]):
            payload = {
                "transcript_id": record["transcript_id"],
                "persona_id": record["persona_id"],
                "persona_label": record["persona_label"],
                "quality": record["quality"],
                "interview_date": record["interview_date"],
                "source_folder": record["source_folder"],
                "memo_round": record["memo_round"],
                "prompt_era": record["prompt_era"],
                "predates_ceef62d": record["predates_ceef62d"],
                "turns": record["turns"],
                "human_overall": record["human_overall"],
                "human_skill_label": record["human_skill_label"],
                "human_dim": {d: record[f"human_dim_{d}"] for d in DIMENSIONS},
                "human_sic": {
                    t: {f: record[f"human_sic_{t}_{f}"] for f in SIC_FIELDS} for t in TIERS
                },
                "source_transcript_docx": record["source_transcript_docx"],
                "source_diagnostic_docx": record["source_diagnostic_docx"],
            }
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    print(f"wrote {JSONL_OUT.relative_to(REPO_ROOT)}  ({len(records)} lines)")


def verify(records: list[dict]) -> int:
    """Structural checks over the built records. Returns the number of failures."""
    failures: list[str] = []

    def check(condition: bool, message: str) -> None:
        if not condition:
            failures.append(message)

    check(len(records) == 16, f"expected 16 transcripts, got {len(records)}")
    ids = [r["transcript_id"] for r in records]
    check(len(set(ids)) == len(ids), "duplicate transcript_id")

    personas = {r["persona_id"] for r in records}
    check(personas == set(p for p, _ in SPEAKER_TO_PERSONA.values()), f"persona coverage: {sorted(personas)}")

    # Full 4 personas x 2 qualities x 2 dates grid.
    for persona_id, _ in SPEAKER_TO_PERSONA.values():
        for quality in ("good", "bad"):
            for meta in ROUNDS.values():
                cell = [
                    r
                    for r in records
                    if r["persona_id"] == persona_id
                    and r["quality"] == quality
                    and r["interview_date"] == meta["date"]
                ]
                check(len(cell) == 1, f"grid cell {persona_id}/{quality}/{meta['date']} has {len(cell)} rows")

    sic_totals = {
        "alex_martinez": (5, 5, 5),
        "michael_mike_alvarez": (3, 4, 4),
        "sarah_donnelly": (4, 4, 4),
        "thomas_tom_caldwell": (4, 4, 4),
    }

    for record in records:
        tid = record["transcript_id"]
        check(1.0 <= record["human_overall"] <= 10.0, f"{tid}: overall out of range")
        for dimension in DIMENSIONS:
            value = record[f"human_dim_{dimension}"]
            check(value is not None and 1.0 <= value <= 10.0, f"{tid}: {dimension} out of range")
        expected = sic_totals[record["persona_id"]]
        for tier, total in zip(TIERS, expected):
            found = record[f"human_sic_{tier}_found"]
            check(
                record[f"human_sic_{tier}_total"] == total,
                f"{tid}: {tier} total {record[f'human_sic_{tier}_total']} != sic_key {total}",
            )
            check(found <= total, f"{tid}: {tier} found {found} > total {total}")
            check(
                0 <= record[f"human_sic_{tier}_pct"] <= 100,
                f"{tid}: {tier} pct out of range",
            )
            check(
                record[f"human_sic_{tier}_status"]
                in {"full", "partial", "not_accessed_insufficient_framing", "not_accessed_appropriate_restraint"},
                f"{tid}: {tier} bad status",
            )
        check(len(record["turns"]) > 0, f"{tid}: no turns")
        for turn in record["turns"]:
            check(turn["role"] in {"user", "assistant"}, f"{tid}: bad role {turn['role']}")
            check(bool(turn["text"].strip()), f"{tid}: empty turn text")

    if failures:
        print(f"\nVERIFY FAILED ({len(failures)}):", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
    else:
        total_turns = sum(len(r["turns"]) for r in records)
        print(f"verify OK: 16 transcripts, 4 personas x 2 qualities x 2 dates, {total_turns} turns")
    return len(failures)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true", help="run structural checks after building")
    args = parser.parse_args()

    records = discover()
    attach_scores(records)
    write_workbook(records)
    write_jsonl(records)
    if args.verify:
        return 1 if verify(records) else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
