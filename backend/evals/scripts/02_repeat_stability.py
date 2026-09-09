"""B1 — repeat-scoring stability. Collection only; analysis is a separate script.

Scores every golden transcript N times with the same model at temperature 0 and
writes one JSONL record per call. Temperature 0 is not determinism, and the
spread this measures is the noise floor every later comparison sits on: an
effect smaller than the SEM found here is not a finding.

    uv run --group evals python -m evals.scripts.02_repeat_stability \
        --n 15 --model gpt-4o --max-usd 20

Re-running with the same arguments makes zero API calls — see evals/lib/cache.py.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from app.api.eval import sanitize_transcript
from app.config import get_settings
from app.evaluation.iqr_scorer import convert_transcript_to_iqr
from evals.lib.cache import call_key, sha256_obj
from evals.lib.golden import load_golden
from evals.lib.runner import CallSpec, RunConfig, Runner
from evals.lib.scoring import (
    iqr_prompt_sha,
    iqr_schema_sha,
    make_scorers,
    score_iqr,
    score_sic,
    sic_prompt_sha,
    sic_rubric_sha,
    sic_schema_sha,
)


def build_specs(frame, n_repeats: int, model: str, temperature: float) -> tuple[list[CallSpec], dict]:
    """One spec per (transcript, run_idx, scorer).

    The cache key carries content hashes of the prompt, the SIC key and the
    output schema, so editing any of them invalidates exactly the affected calls
    rather than silently reusing results from before the edit.
    """
    iqr_p, sic_p = iqr_prompt_sha(), sic_prompt_sha()
    iqr_s, sic_s = iqr_schema_sha(), sic_schema_sha()

    specs: list[CallSpec] = []
    payloads: dict[str, dict] = {}

    for row in frame.itertuples():
        # Sanitize once, before both scorers, exactly as the production endpoint
        # does — so B1 measures the pipeline students actually get.
        turns = sanitize_transcript(row.turns)
        transcript_sha = sha256_obj(turns)
        transcript = convert_transcript_to_iqr(
            {
                "turns": turns,
                "persona_key": row.persona_id,
                "session_id": row.transcript_id,
                "metadata": {},
            }
        )
        rubric = sic_rubric_sha(row.persona_id)

        for run_idx in range(n_repeats):
            common = dict(
                model=model,
                temperature=temperature,
                transcript_sha=transcript_sha,
                persona_id=row.persona_id,
                run_idx=run_idx,
            )
            meta = {
                "transcript_id": row.transcript_id,
                "persona_id": row.persona_id,
                "quality": row.quality,
                "prompt_era": row.prompt_era,
                "turn_count": int(row.turn_count),
                "run_idx": run_idx,
                "model": model,
                "human_overall": float(row.human_overall),
            }

            k_iqr = call_key(scorer="iqr", prompt_sha=iqr_p, schema_sha=iqr_s, **common)
            specs.append(CallSpec(kind="iqr", key=k_iqr, meta=meta))
            payloads[k_iqr] = {"transcript": transcript}

            k_sic = call_key(
                scorer="sic", prompt_sha=sic_p, schema_sha=sic_s, rubric_sha=rubric, **common
            )
            specs.append(CallSpec(kind="sic", key=k_sic, meta=meta))
            payloads[k_sic] = {"persona_id": row.persona_id, "turns": turns}

    return specs, payloads


async def main() -> int:
    parser = argparse.ArgumentParser(description="B1 repeat-scoring stability (collection)")
    parser.add_argument("--n", type=int, default=15, help="repeat runs per transcript")
    parser.add_argument("--model", default="gpt-4o")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--max-usd", type=float, default=20.0)
    parser.add_argument("--limit", type=int, default=None, help="first N transcripts only (pilot runs)")
    parser.add_argument("--name", default="b1_stability")
    args = parser.parse_args()

    get_settings()  # bridges OPENAI_API_KEY into os.environ for the scorers

    frame = load_golden()
    if args.limit:
        frame = frame.head(args.limit)

    specs, payloads = build_specs(frame, args.n, args.model, args.temperature)
    iqr_scorer, sic_scorer = make_scorers(args.model, args.temperature)

    async def executor(spec: CallSpec) -> dict:
        payload = payloads[spec.key]
        if spec.kind == "iqr":
            return await score_iqr(iqr_scorer, payload["transcript"])
        return await score_sic(sic_scorer, payload["persona_id"], payload["turns"])

    runner = Runner(
        RunConfig(
            name=args.name,
            model=args.model,
            concurrency=args.concurrency,
            max_usd=args.max_usd,
            temperature=args.temperature,
        )
    )
    print(
        f"{len(frame)} transcripts x {args.n} runs x 2 scorers = {len(specs)} calls "
        f"| model={args.model} | ceiling ${args.max_usd:.2f}"
    )

    records = await runner.run(specs, executor)

    errors = [r for r in records if r.get("error")]
    fallbacks = [r for r in records if r.get("model_used") not in (None, args.model)]
    print(f"\nrun      : {runner.run_id}")
    print(f"records  : {len(records)}/{len(specs)}  errors {len(errors)}  fallbacks {len(fallbacks)}")
    print(f"spent    : ${runner.spent_usd:.4f}  api calls {runner.api_calls}  cache {runner.cache.stats}")
    print(f"output   : {runner.jsonl_path}")
    if errors:
        for r in errors[:5]:
            print(f"  ! {r.get('transcript_id')} {r['kind']}: {str(r['error'])[:110]}")
    return 1 if len(records) < len(specs) else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
