# Evaluation results

Latest figure from each study. Every number links to a dated report carrying the
git SHA, prompt versions, judge model, N and spend that produced it.

Collection and analysis are separate programs joined by an append-only JSONL
file, so any statistic here can be re-derived — or re-cut — without spending
again. Re-running a collection script with unchanged arguments makes zero API
calls.

## B1 — Judge test–retest reliability

[2026-09-08_b1_stability.md](reports/2026-09-08_b1_stability.md) · gpt-4o, prompt v2 ·
16 transcripts × 15 runs = 480 judgements · $9.02

| metric | value |
| --- | --- |
| SEM (overall, 10-pt scale) | **0.135** |
| MDC95 | **0.373 points** |
| Mean within-transcript SD | 0.097 (95% CI 0.052–0.144) |
| True between-transcript spread ÷ SEM | 2.2× among strong interviews · 5.9× among weak |
| Band flip rate | 6.2% (1/16) |
| SIC per-item agreement | 98.2% · Fleiss κ 0.963 · Gwet AC1 0.963 |
| Errors / silent fallbacks | 0 / 0 of 480 |

**Production decision: single-run scoring stands.** MDC95 of 0.37 points is well
inside the ~1 point that would change what a student is told, so scoring each
interview once is defensible; k-run median scoring is not needed.

One dimension does not discriminate at the top of the range. Among the eight
strong interviews the judge gave `probing_and_follow_up_depth` a score of 9.0 to
seven of them and 8.97 to the eighth, so the true between-transcript spread is
0.00 against an SEM of 0.046. That is a ceiling effect rather than instability —
probing is the *most* stable cell in the study. The consequence is the same
either way: the metric cannot rank one strong interview above another, so it
tells a capable student nothing about what to improve. The same reading applies
more mildly to framing and question quality, which produce only three distinct
average scores across those eight interviews.

Per-dimension ICC, pooled and within stratum, is in the report's own ICC table,
where the caption carries the caveats it needs.

> Test–retest reliability of the GPT-4o interview-quality judge: SEM 0.14 and
> MDC95 0.37 points on a 10-point scale, across 15 repeat scorings of 16 graded
> transcripts (480 judgements).

## B2 — Primary vs fallback judge

[2026-09-08_b2_model_agreement.md](reports/2026-09-08_b2_model_agreement.md) ·
gpt-4o vs gpt-4o-mini · 15 runs each · $0.65

| model | scorer | schema-valid output |
| --- | --- | --- |
| gpt-4o | IQR | 240/240 (100%) |
| gpt-4o | SIC | 240/240 (100%) |
| gpt-4o-mini | IQR | **7/240 (2.9%)** |
| gpt-4o-mini | SIC | 240/240 (100%) |

**The IQR fallback is not a degraded judge, it is a broken one.** 232 of the 233
failures are one hallucination — `listening_interpretation_and_conversational_stewardship`
for the schema's `listening_interpretation_and_stewardship`. When gpt-4o errors,
the student does not receive a slightly worse grade; they receive an HTTP 500.

The cause is a difference in how the two chains are built: SIC uses
`with_structured_output`, where the API enforces the schema, while IQR parses
free text with `PydanticOutputParser`. On gpt-4o that difference is invisible.

Where both models produce complete data (SIC), agreement is moderate rather than
interchangeable: 81.0% agreement, Cohen κ 0.619, AC1 0.623, with mini crediting
students less often (42.0% vs 49.0% of items elicited).

| model | $/IQR+SIC pair | p50 latency | p95 latency |
| --- | --- | --- | --- |
| gpt-4o | $0.038 | 9.7 s | 15.6 s |
| gpt-4o-mini | $0.003 | 6.5 s | 8.5 s |

IQR agreement statistics are absent by necessity, not omission — 7 parseable
secondary-model results is far too few to correlate.

## B4 — Retrieval quality, latency and abstention

Not yet run. Instrumentation is live: `retrieval_events` records `embed_ms` and
`search_ms` separately for every `/realtime/retrieve` call.

Early reading from the instrumentation smoke test (n=3, not a result): the
OpenAI embedding round trip took 541–707 ms warm while the pgvector search over
2,251 chunks took 8–42 ms. If that holds, retrieval latency is almost entirely
the embedding hop, and tuning the ANN index would buy little.
