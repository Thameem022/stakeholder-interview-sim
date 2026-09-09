# B1 — Repeat-scoring stability

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
| transcripts | 16 |
| repeats_per_transcript | 15 |
| iqr_judgements | 240 |
| sic_judgements | 240 |

## Question

How far does the same transcript's score move between identical scoring runs, and is a single run enough to grade a student on?


## Method

Each of the 16 graded transcripts was scored 15 times by `gpt-4o` at temperature 0 with the fallback to gpt-4o-mini disabled, so every judgement is attributable to the primary model. Transcripts were sanitized once before both scorers, exactly as the production endpoint does. SIC went through `grade_raw`, which returns per-item labels and skips the cosmetic enrichment call.



Absolute reliability leads because it does not depend on how spread out this sample happens to be. SEM is the within-subject root mean square from a one-way ANOVA; MDC95 = 1.96 x sqrt(2) x SEM is how far two scorings of one transcript must differ before the difference is more than noise.


## Headline

- **SEM 0.135 points** on the 10-point overall score

- **MDC95 0.373 points** — two runs on the same transcript differ by more than this 5% of the time by chance alone

- Mean within-transcript SD 0.097 (95% CI 0.052-0.144), max 0.229

- Band flip rate **6.2%** of transcripts (1/16) landed in more than one skill band

- Production decision: **single-run scoring stands**


## Absolute reliability by metric

| metric | sem | mdc95 | mean_within_sd | max_within_sd | mean_range | max_range | k_for_mdc_1pt | n |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| overall | 0.135 | 0.373 | 0.097 | 0.229 | 0.281 | 1.000 | 1 | 240 |
| framing_and_stakeholder_fit | 0.082 | 0.227 | 0.029 | 0.254 | 0.062 | 0.500 | 1 | 240 |
| question_quality_and_precision | 0.136 | 0.378 | 0.067 | 0.352 | 0.188 | 1.000 | 1 | 240 |
| probing_and_follow_up_depth | 0.189 | 0.524 | 0.100 | 0.563 | 0.344 | 2.000 | 1 | 240 |
| listening_interpretation_and_stewardship | 0.194 | 0.539 | 0.129 | 0.458 | 0.406 | 1.000 | 1 | 240 |


## Discrimination — real spread against measurement noise

For each stratum: the SEM of a single scoring, and the estimated standard deviation of the *true* scores across comparable interviews. The ratio is what matters — a metric whose true spread is smaller than its own noise cannot rank one interview above another, however consistent it is.



`distinct_means` counts how many different average scores the metric actually produced across the transcripts in that stratum. A value of 1 or 2 means the metric is at a ceiling or floor and is not measuring anything within that group.

| metric | stratum | n_transcripts | sem | true_between_sd | spread_over_noise | distinct_means |
| --- | --- | --- | --- | --- | --- | --- |
| probing_and_follow_up_depth | bad | 8 | 0.263 | 1.084 | 4.117 | 8 |
| listening_interpretation_and_stewardship | bad | 8 | 0.251 | 1.254 | 5.003 | 8 |
| overall | bad | 8 | 0.174 | 1.023 | 5.883 | 8 |
| question_quality_and_precision | bad | 8 | 0.171 | 1.061 | 6.214 | 6 |
| framing_and_stakeholder_fit | bad | 8 | 0.073 | 0.942 | 12.865 | 4 |
| probing_and_follow_up_depth | good | 8 | 0.046 | 0.000 | 0.000 | 2 |
| framing_and_stakeholder_fit | good | 8 | 0.090 | 0.179 | 2.000 | 3 |
| question_quality_and_precision | good | 8 | 0.090 | 0.179 | 2.000 | 3 |
| overall | good | 8 | 0.078 | 0.169 | 2.181 | 5 |
| listening_interpretation_and_stewardship | good | 8 | 0.113 | 0.350 | 3.095 | 5 |


## ICC — pooled and within stratum

The corpus splits into eight interviews the grader scored 8.2-9.0 and eight at 1.5-5.5, so between-transcript variance is enormous and the pooled ICC mostly confirms the judge can tell good from bad. The within-stratum rows are the ones that describe behaviour among comparable interviews; with n=8 their intervals are wide, and that is stated rather than hidden.

| metric | stratum | n_transcripts | icc_1_1 | ci_low | ci_high | icc_1_k |
| --- | --- | --- | --- | --- | --- | --- |
| framing_and_stakeholder_fit | pooled | 16 | 0.999 | 1.000 | 1.000 | 1.000 |
| framing_and_stakeholder_fit | good only | 8 | 0.800 | 0.620 | 0.940 | 0.984 |
| framing_and_stakeholder_fit | bad only | 8 | 0.994 | 0.990 | 1.000 | 1.000 |
| listening_interpretation_and_stewardship | pooled | 16 | 0.996 | 0.990 | 1.000 | 1.000 |
| listening_interpretation_and_stewardship | good only | 8 | 0.905 | 0.800 | 0.980 | 0.993 |
| listening_interpretation_and_stewardship | bad only | 8 | 0.962 | 0.910 | 0.990 | 0.997 |
| overall | pooled | 16 | 0.998 | 1.000 | 1.000 | 1.000 |
| overall | good only | 8 | 0.826 | 0.660 | 0.950 | 0.986 |
| overall | bad only | 8 | 0.972 | 0.930 | 0.990 | 0.998 |
| probing_and_follow_up_depth | pooled | 16 | 0.997 | 0.990 | 1.000 | 1.000 |
| probing_and_follow_up_depth | good only | 8 | -0.000 | -0.040 | 0.180 | -0.000 |
| probing_and_follow_up_depth | bad only | 8 | 0.944 | 0.880 | 0.990 | 0.996 |
| question_quality_and_precision | pooled | 16 | 0.997 | 0.990 | 1.000 | 1.000 |
| question_quality_and_precision | good only | 8 | 0.800 | 0.620 | 0.940 | 0.984 |
| question_quality_and_precision | bad only | 8 | 0.975 | 0.940 | 0.990 | 0.998 |


## Band stability per transcript

`skill_label` is an unconstrained string in the schema, so raw label variety is reported separately from movement between numeric bands.

| transcript_id | quality | mean_score | sd | n_bands | bands | n_raw_labels | raw_labels |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-06-15_sarah_bad | bad | 1.367 | 0.229 | 1 | Weak | 1 | Weak Interviewer |
| 2026-06-06_mike_bad | bad | 3.133 | 0.229 | 1 | Developing | 1 | Developing Interviewer |
| 2026-06-15_mike_bad | bad | 2.100 | 0.207 | 1 | Weak | 2 | Surface-Level Interviewer | Weak Interviewer |
| 2026-06-06_alex_good | good | 8.860 | 0.203 | 2 | Exemplary/Strong | 1 | Strong Interviewer |
| 2026-06-15_alex_bad | bad | 2.000 | 0.189 | 1 | Weak | 1 | Weak Interviewer |
| 2026-06-06_tom_bad | bad | 3.433 | 0.176 | 1 | Developing | 1 | Developing Interviewer |
| 2026-06-06_sarah_bad | bad | 3.533 | 0.129 | 1 | Developing | 1 | Developing Interviewer |
| 2026-06-06_alex_bad | bad | 4.540 | 0.106 | 1 | Developing | 1 | Developing Interviewer |
| 2026-06-06_sarah_good | good | 8.947 | 0.083 | 1 | Exemplary | 1 | Strong Interviewer |
| 2026-06-06_mike_good | good | 9.000 | 0.000 | 1 | Exemplary | 1 | Strong Interviewer |
| 2026-06-06_tom_good | good | 8.500 | 0.000 | 1 | Strong | 1 | Strong Interviewer |
| 2026-06-15_alex_good | good | 9.000 | 0.000 | 1 | Exemplary | 1 | Strong Interviewer |
| 2026-06-15_mike_good | good | 9.000 | 0.000 | 1 | Exemplary | 1 | Strong Interviewer |
| 2026-06-15_sarah_good | good | 8.800 | 0.000 | 1 | Exemplary | 2 | Strong Interviewer | Strong Operational Interviewer |
| 2026-06-15_tom_bad | bad | 2.500 | 0.000 | 1 | Weak | 2 | Developing Interviewer | Weak Interviewer |
| 2026-06-15_tom_good | good | 8.800 | 0.000 | 1 | Exemplary | 1 | Strong Interviewer |


## SIC per-item agreement

Kappa and AC1 are shown together because most items are un-elicited in most transcripts. Under that prevalence imbalance kappa collapses toward zero even at very high observed agreement, which would read as an unstable judge when it is not; AC1 is the prevalence-robust counterpart.

| scope | n_item_transcripts | pct_agreement | fleiss_kappa | gwet_ac1 | pct_unstable |
| --- | --- | --- | --- | --- | --- |
| all | 200 | 0.982 | 0.963 | 0.963 | 0.025 |
| tier 1 | 64 | 0.989 | 0.975 | 0.979 | 0.016 |
| tier 2 | 68 | 0.977 | 0.952 | 0.957 | 0.029 |
| tier 3 | 68 | 0.979 | 0.958 | 0.958 | 0.029 |


## Least stable SIC items

Items whose `elicited` label disagreed across runs most often — direct input for prompt revision.

| transcript_id | chunk_id | tier | type | quality | n_runs | n_elicited | disagreement |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-06-06_mike_good | mike_a_t3_resilience_as_burden | 3 | signal | good | 15 | 7 | 0.467 |
| 2026-06-06_alex_good | alex_m_t2_temporal_mismatch | 2 | fact | good | 15 | 7 | 0.467 |
| 2026-06-15_tom_bad | tom_c_t2_regulation_lag | 2 | fact | bad | 15 | 7 | 0.467 |
| 2026-06-15_alex_bad | alex_m_t1_institutional_process | 1 | fact | bad | 15 | 5 | 0.333 |
| 2026-06-06_mike_good | mike_a_t3_trust_erosion | 3 | signal | good | 15 | 11 | 0.267 |
| 2026-06-15_mike_good | mike_a_t3_loss_of_belonging | 3 | signal | good | 15 | 3 | 0.200 |
| 2026-06-06_mike_good | mike_a_t2_planning_disconnect | 2 | fact | good | 15 | 12 | 0.200 |
| 2026-06-06_alex_good | alex_m_t1_formal_strategies | 1 | fact | good | 15 | 13 | 0.133 |
| 2026-06-06_sarah_good | sarah_d_t3_exit_consideration | 3 | signal | good | 15 | 1 | 0.067 |
| 2026-06-15_alex_good | alex_m_t2_temporal_mismatch | 2 | fact | good | 15 | 14 | 0.067 |
| 2026-06-15_mike_bad | mike_a_t1_places_that_flood_first | 1 | fact | bad | 15 | 0 | 0.000 |
| 2026-06-15_mike_bad | mike_a_t1_routine_adjustments | 1 | fact | bad | 15 | 0 | 0.000 |
| 2026-06-15_mike_bad | mike_a_t2_informal_mutual_aid | 2 | fact | bad | 15 | 0 | 0.000 |
| 2026-06-15_mike_bad | mike_a_t2_informal_prioritization | 2 | fact | bad | 15 | 0 | 0.000 |
| 2026-06-15_mike_bad | mike_a_t2_planning_disconnect | 2 | fact | bad | 15 | 0 | 0.000 |


## Tier percentage spread

Tier arithmetic is deterministic given the item grades, so this variance traces entirely back to the LLM labels above.

| tier | mean_within_sd | max_within_sd |
| --- | --- | --- |
| 1 | 1.050 | 9.759 |
| 2 | 2.422 | 12.910 |
| 3 | 2.397 | 21.547 |


## Where instability concentrates

- Within-transcript SD vs transcript length: r = -0.054

- Within-transcript SD vs mean score: r = -0.677

- Mean SD among the 8 lowest-scoring transcripts: 0.158

- Mean SD among the 8 highest-scoring transcripts: 0.036

- Transcripts identical across all 15 runs (SD = 0): 7 of 16



The judge is markedly **less** consistent on the weaker interviews than the stronger ones. That is the benign direction: a strong interview is graded the same way every time, and the residual movement sits where scores are low enough that a fraction of a point does not change what the student is told. It would be the worrying direction if the spread sat near a band boundary instead.

| transcript_id | sd | mean | turns | quality |
| --- | --- | --- | --- | --- |
| 2026-06-15_sarah_bad | 0.229 | 1.367 | 14 | bad |
| 2026-06-06_mike_bad | 0.229 | 3.133 | 52 | bad |
| 2026-06-15_mike_bad | 0.207 | 2.100 | 15 | bad |
| 2026-06-06_alex_good | 0.203 | 8.860 | 39 | good |
| 2026-06-15_alex_bad | 0.189 | 2.000 | 17 | bad |
| 2026-06-06_tom_bad | 0.176 | 3.433 | 30 | bad |
| 2026-06-06_sarah_bad | 0.129 | 3.533 | 46 | bad |
| 2026-06-06_alex_bad | 0.106 | 4.540 | 41 | bad |
| 2026-06-06_sarah_good | 0.083 | 8.947 | 21 | good |
| 2026-06-06_mike_good | 0.000 | 9.000 | 30 | good |
| 2026-06-06_tom_good | 0.000 | 8.500 | 62 | good |
| 2026-06-15_alex_good | 0.000 | 9.000 | 16 | good |
| 2026-06-15_mike_good | 0.000 | 9.000 | 22 | good |
| 2026-06-15_sarah_good | 0.000 | 8.800 | 38 | good |
| 2026-06-15_tom_bad | 0.000 | 2.500 | 35 | bad |
| 2026-06-15_tom_good | 0.000 | 8.800 | 15 | good |


## Operational health

- Errors: 0 of 480 calls

- Silent fallbacks to the secondary model: 0 (strict mode should make this 0)

- `system_fingerprint` values seen: 3 — fp_0ea3a61dc5, fp_1812855600, fp_c9a0e786b8

- Spend: $9.0221 over 472 API calls

- Median call latency: 9685 ms (p95 15612 ms)



More than one fingerprint means part of the measured spread is OpenAI's serving backend changing under the run rather than the judge itself.


## Caveats

- All 16 transcripts predate commit `ceef62d` and the v2 prompt (`51f2e82`), so they were produced by an older persona build. This bounds how far the figure generalises to interviews students run today; it does not affect the measurement of the current judge's self-consistency, which is what B1 claims.

- Within-stratum ICC rests on n=8 transcripts per stratum.

- Temperature 0 is not determinism; that is the premise of the study, not a flaw in it.
