# B2 — gpt-4o vs gpt-4o-mini

| field | value |
| --- | --- |
| date | 2026-09-08 |
| git_sha | ceef62d9cd3d (dirty) |
| run_id | 20260908T195453Z_b1_stability_gpt-4o_2caf21 |
| model | gpt-4o |
| temperature | 0.0 |
| harness | 1 |
| api_calls | 472 |
| spend_usd | 9.0221 |
| wall_seconds | 1043.8 |
| primary_run | 20260908T195453Z_b1_stability_gpt-4o_2caf21 |
| secondary_run | 20260908T201402Z_b1_stability_gpt-4o-mini_3f30b3 |
| transcripts | 4 |
| combined_spend_usd | 9.6704 |

## Question

Both scorers fall back to `gpt-4o-mini` on any exception. What does a student lose on the runs that take that path?


## Method

The same 4 transcripts were scored 15 times by each model at temperature 0 with fallback disabled. Matching the repeat count matters: it makes each model's own test-retest reliability available, which the correction below requires. Agreement is reported on run medians and on single runs, because a single run is what a student actually receives.


## Headline

- IQR schema-valid output: **100.0%** for `gpt-4o` vs **2.9%** for `gpt-4o-mini`

- SIC schema-valid output: **100.0%** for `gpt-4o-mini` (structured output, schema enforced by the API)

- IQR agreement (rho, bias, weighted kappa) is **not computable**: only 7 of 240 `gpt-4o-mini` IQR calls returned schema-valid output, far too few to correlate against. See the output-validity table below.

- SIC per-item Cohen kappa: **0.619** (AC1 0.623, 81.0% agreement)

- Verdict: **the IQR fallback is not a degraded judge, it is a broken one — `gpt-4o-mini` produced schema-valid output on only 2.9% of calls. When gpt-4o errors, the student does not get a slightly worse grade; they get an HTTP 500**


## Why the correction is not optional

Agreement between two noisy judges is capped by how well each agrees with itself. An observed rho of 0.85 could mean the models genuinely disagree, or simply that both are individually unstable — and no uncorrected correlation can tell those apart. Both reliabilities come straight from the B1-style repeat runs, so the correction costs nothing extra to compute.



If the corrected figure approaches 1.0, the models agree as well as they possibly could and the residual disagreement is noise rather than disposition.


## Output validity — can each model answer at all?

The IQR chain ends in a `PydanticOutputParser` over free text, while SIC uses `with_structured_output`, where the schema is enforced by the API rather than hoped for. That difference dominates this study.

| model | scorer | calls | succeeded | failed | success_rate | dominant_failure |
| --- | --- | --- | --- | --- | --- | --- |
| gpt-4o | iqr | 240 | 240 | 0 | 1.000 |  |
| gpt-4o | sic | 240 | 240 | 0 | 1.000 |  |
| gpt-4o-mini | iqr | 240 | 7 | 233 | 0.029 | OutputParserException: F |
| gpt-4o-mini | sic | 240 | 240 | 0 | 1.000 |  |


## Agreement by metric

| comparison | n |
| --- | --- |
| IQR comparison not run | 0 |


## Skill band confusion

| note |
| --- |
| not computed — see IQR output validity |


## SIC item agreement

Modal `elicited` label per item across the 15 runs of each model. AC1 sits beside kappa because most items are un-elicited in most transcripts.

| n_item_transcripts | pct_agreement | cohen_kappa | gwet_ac1 | gpt-4o_elicited_rate | gpt-4o-mini_elicited_rate |
| --- | --- | --- | --- | --- | --- |
| 200 | 0.810 | 0.619 | 0.623 | 0.490 | 0.420 |


## Operating characteristics

| model | api_calls | total_usd | usd_per_iqr_sic_pair | p50_latency_ms | p95_latency_ms |
| --- | --- | --- | --- | --- | --- |
| gpt-4o | 472 | 9.022 | 0.038 | 9685.250 | 15612.030 |
| gpt-4o-mini | 541 | 0.648 | 0.003 | 6541.800 | 8526.480 |


## What to do about it

The two scorers differ in exactly one respect that matters here. SIC builds its chain as `llm.with_structured_output(SICGradingResult)`, so the schema is enforced by the API and the model cannot return a value outside it. IQR builds `prompt | llm | PydanticOutputParser(...)`, which asks for JSON in the prompt and validates whatever free text comes back.



On `gpt-4o` that difference is invisible — it follows the instruction every time. On `gpt-4o-mini` it is fatal: 233 of the failures are the same hallucination, `listening_interpretation_and_conversational_stewardship` in place of the schema's `listening_interpretation_and_stewardship` — an extra word inserted into the longest literal in the enum.



Switching the IQR chain to `with_structured_output` would make the fallback usable and cost nothing at inference time. It is deliberately **not** done as part of this study: it changes the primary path too, which would invalidate the B1 measurements collected under the current one. Change it, re-record B1, then re-run this comparison.



Until then the honest description of production behaviour is that the IQR fallback does not exist — it is a code path that converts one failure into another.


## Caveats

- IQR agreement statistics are absent by necessity: only 7 of 240 secondary-model IQR calls parsed, which is far too few to correlate. The SIC comparison below uses complete data on both sides (240/240 each).

- SIC agreement rests on 200 item-transcript pairs across 16 transcripts. A promotion decision should not rest on it.

- Both models scored the same transcripts, all of which predate the v2 prompt and `ceef62d`.

- Bland-Altman limits assume roughly normal differences; with n=16 they are indicative rather than tight.
