# backend/evals

Dev-only evaluation suite. Not shipped — the wheel packages only `app`.

    uv sync --group evals

## Layout

    lib/       golden.py cache.py runner.py scoring.py stats.py report.py
    scripts/   collection and analysis entry points
    runs/      append-only JSONL + manifest.json, one directory per run
    reports/   dated Markdown + CSV
    .cache/    content-addressed call cache (gitignored)

## Running a study

Collection spends money; analysis does not. They are separate programs joined by
`runs/<study>/<run_id>/records.jsonl`, so every statistic can be re-derived
without re-spending.

    # B1 — repeat-scoring stability (~$9, ~17 min)
    uv run --group evals python -m evals.scripts.02_repeat_stability \
        --n 15 --model gpt-4o --max-usd 20
    uv run --group evals python -m evals.scripts.analyze_stability

    # B2 — fallback judge (~$0.65); reuses the collection script
    uv run --group evals python -m evals.scripts.02_repeat_stability \
        --n 15 --model gpt-4o-mini --max-usd 3
    uv run --group evals python -m evals.scripts.03_model_agreement

Re-running a collection script with the same arguments makes **zero** API calls.
Add `--limit 2 --n 2` for a pilot before committing to a full run.

## Why the cache key hashes file contents

`call_key` hashes the prompt text, the SIC key JSON and the output schema — not
the version directory name. Hashing `"v2"` would mean an in-place edit to
`prompts/iqr/v2/system_prompt.txt` kept serving results from before the edit,
indefinitely, with nothing looking wrong.

## Strict mode

Evaluation runs construct scorers with `allow_fallback=False`. Both scorers
otherwise downgrade to `gpt-4o-mini` on any exception without recording it,
which would silently turn a gpt-4o measurement into a mixed one. B2 exists
because of what that path does; see `RESULTS.md`.

## Tests

    uv run --group evals pytest tests/evals -q

No API key, no network, no database. They pin the contracts the studies depend
on: transcript conversion, the SIC tier arithmetic, catalogue sizes, cache
identity and the statistics.
