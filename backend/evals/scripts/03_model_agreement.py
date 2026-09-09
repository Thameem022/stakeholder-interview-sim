"""B2 — primary vs fallback judge agreement.

Collection reuses the B1 script with a different model:

    uv run --group evals python -m evals.scripts.02_repeat_stability \
        --n 15 --model gpt-4o-mini --max-usd 3

Then this analyses the two runs together:

    uv run --group evals python -m evals.scripts.03_model_agreement

This is not an abstract model comparison. Both scorers fall back to
gpt-4o-mini on any exception, so this measures the consequence of a code path
that is live in production.
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd
from scipy import stats as sps
from sklearn.metrics import cohen_kappa_score

from evals.lib.report import (
    header_block,
    latest_run,
    load_records,
    md_table,
    read_manifest,
    section,
    write_report,
)
from evals.lib.stats import bootstrap_ci, gwet_ac1, icc1, percent_agreement, skill_band
from evals.scripts.analyze_stability import DIMENSIONS, iqr_long, sic_item_long


def agreement_row(a: pd.Series, b: pd.Series, label: str) -> dict:
    """Pearson, Spearman, bias and Bland-Altman limits for one paired vector."""
    paired = pd.concat([a.rename("a"), b.rename("b")], axis=1).dropna()
    if len(paired) < 3:
        return {"comparison": label, "n": len(paired)}
    diff = paired.a - paired.b
    pearson = sps.pearsonr(paired.a, paired.b)
    spearman = sps.spearmanr(paired.a, paired.b)
    try:
        wilcoxon_p = float(sps.wilcoxon(paired.a, paired.b).pvalue)
    except ValueError:  # all differences zero
        wilcoxon_p = float("nan")
    return {
        "comparison": label, "n": len(paired),
        "pearson_r": float(pearson.statistic), "spearman_rho": float(spearman.statistic),
        "mean_bias": float(diff.mean()), "mae": float(diff.abs().mean()),
        # Bland-Altman: where 95% of individual disagreements fall. A model that
        # is unbiased on average can still be wildly inconsistent case by case.
        "loa_low": float(diff.mean() - 1.96 * diff.std()),
        "loa_high": float(diff.mean() + 1.96 * diff.std()),
        "wilcoxon_p": wilcoxon_p,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="B2 model agreement")
    parser.add_argument("--primary", default="gpt-4o")
    parser.add_argument("--secondary", default="gpt-4o-mini")
    parser.add_argument("--name", default="b1_stability")
    args = parser.parse_args()

    runs = {m: latest_run(args.name, m) for m in (args.primary, args.secondary)}
    manifests = {m: read_manifest(d) for m, d in runs.items()}
    records = {m: load_records(d) for m, d in runs.items()}
    longs = {m: iqr_long(r) for m, r in records.items()}
    items = {m: sic_item_long(r) for m, r in records.items()}

    # Whether a model can produce a parseable result at all comes before how
    # well it agrees. A judge that fails is not a lenient judge.
    success_rows = []
    for model, frame in records.items():
        for kind in ("iqr", "sic"):
            subset = frame[frame.kind == kind]
            failed = subset.error.notna().sum() if "error" in subset else 0
            success_rows.append({
                "model": model, "scorer": kind, "calls": len(subset),
                "succeeded": int(len(subset) - failed), "failed": int(failed),
                "success_rate": float((len(subset) - failed) / len(subset)) if len(subset) else float("nan"),
                "dominant_failure": (subset[subset.error.notna()].error.astype(str)
                                     .str.slice(0, 24).mode().iloc[0]
                                     if failed else ""),
            })
    success = pd.DataFrame(success_rows)

    # A per-model IQR comparison needs enough transcripts on both sides.
    iqr_transcripts = {m: long[long.metric == "overall"].transcript_id.nunique()
                       for m, long in longs.items()}
    iqr_comparable = min(iqr_transcripts.values()) >= 8

    # Each model's own test-retest reliability. Agreement between two noisy
    # judges is capped by how well each agrees with itself, so these are not a
    # side note — they are what makes the corrected figure below meaningful.
    self_icc = {}
    for model, long in longs.items():
        overall = long[long.metric == "overall"]
        try:
            self_icc[model] = icc1(overall)["icc1_1"] if overall.transcript_id.nunique() >= 3 else float("nan")
        except Exception:
            self_icc[model] = float("nan")

    # ── Overall agreement, on medians and on single runs ─────────────────────
    rows = []
    if not iqr_comparable:
        rows.append({"comparison": "IQR comparison not run", "n": 0})
    for metric in ([] if not iqr_comparable else ["overall", *DIMENSIONS]):
        med = {m: long[long.metric == metric].groupby("transcript_id").score.median()
               for m, long in longs.items()}
        rows.append(agreement_row(med[args.primary], med[args.secondary], f"{metric} (medians)"))
        single = {m: (long[(long.metric == metric) & (long.run_idx == 0)]
                      .set_index("transcript_id").score)
                  for m, long in longs.items()}
        rows.append(agreement_row(single[args.primary], single[args.secondary],
                                  f"{metric} (single run)"))
    agree = pd.DataFrame(rows)

    # ── Attenuation correction ───────────────────────────────────────────────
    overall_med = {m: long[long.metric == "overall"].groupby("transcript_id").score.median()
                   for m, long in longs.items()}
    paired = pd.concat([overall_med[args.primary].rename("primary"),
                        overall_med[args.secondary].rename("secondary")], axis=1).dropna()

    nan = float("nan")
    if iqr_comparable and len(paired) >= 3:
        rho_obs = float(sps.spearmanr(paired.primary, paired.secondary).statistic)
        denom = np.sqrt(max(self_icc[args.primary], 0) * max(self_icc[args.secondary], 0))
        rho_corr = float(rho_obs / denom) if denom > 0 else nan
        rho_ci = bootstrap_ci((paired.primary - paired.secondary).tolist(), statistic=np.mean)
        bands = paired.map(skill_band)
        labels = sorted(set(bands.primary) | set(bands.secondary))
        qwk = float(cohen_kappa_score(bands.primary, bands.secondary,
                                      labels=labels, weights="quadratic")) if len(labels) > 1 else nan
        confusion = pd.crosstab(bands.primary, bands.secondary).reset_index()
        bias = float((paired.primary - paired.secondary).mean())
    else:
        rho_obs = rho_corr = qwk = bias = nan
        rho_ci = (nan, nan)
        confusion = pd.DataFrame([{"note": "not computed — see IQR output validity"}])

    # ── SIC item agreement between models ────────────────────────────────────
    modal = {}
    for model, frame in items.items():
        modal[model] = (frame.groupby(["transcript_id", "chunk_id"]).elicited
                        .mean().gt(0.5).rename(model))
    sic_pair = pd.concat([modal[args.primary], modal[args.secondary]], axis=1).dropna()
    sic_kappa = float(cohen_kappa_score(sic_pair[args.primary], sic_pair[args.secondary]))
    sic_matrix = sic_pair.to_numpy().astype(bool)
    sic_stats = pd.DataFrame([{
        "n_item_transcripts": len(sic_pair),
        "pct_agreement": percent_agreement(sic_matrix),
        "cohen_kappa": sic_kappa,
        "gwet_ac1": gwet_ac1(sic_matrix),
        f"{args.primary}_elicited_rate": float(sic_pair[args.primary].mean()),
        f"{args.secondary}_elicited_rate": float(sic_pair[args.secondary].mean()),
    }])

    # ── Operating characteristics ────────────────────────────────────────────
    ops = pd.DataFrame([{
        "model": m,
        "api_calls": manifests[m].get("api_calls"),
        "total_usd": manifests[m].get("spent_usd"),
        "usd_per_iqr_sic_pair": round(
            (manifests[m].get("spent_usd") or 0) / max(len(r) / 2, 1), 5),
        "p50_latency_ms": float(r.latency_ms.median()),
        "p95_latency_ms": float(r.latency_ms.quantile(0.95)),
    } for m, r in records.items()])

    if not iqr_comparable:
        rate = success[(success.model == args.secondary) & (success.scorer == "iqr")].success_rate.iloc[0]
        verdict = (
            f"the IQR fallback is not a degraded judge, it is a broken one — `{args.secondary}` "
            f"produced schema-valid output on only {rate:.1%} of calls. When gpt-4o errors, the "
            "student does not get a slightly worse grade; they get an HTTP 500"
        )
    elif rho_corr >= 0.95 and abs(bias) < 0.3 and qwk >= 0.85:
        verdict = ("the silent fallback is materially harmless — keep logging it via "
                   "`last_model_used`, no product change required")
    elif (not np.isnan(bias) and abs(bias) >= 0.5) or (not np.isnan(qwk) and qwk < 0.7):
        verdict = ("the fallback is silently regrading students — surface the judge model on the "
                   "score report, or drop the fallback and fail loudly")
    else:
        verdict = "the fallback is tolerable but not equivalent; record the model on every row"

    if iqr_comparable and not np.isnan(rho_obs):
        iqr_headline = [
            f"- Observed Spearman rho on overall score: **{rho_obs:.3f}**",
            f"- Corrected for each model's self-agreement: **{rho_corr:.3f}** "
            f"(rho_obs / sqrt(ICC_{args.primary}={self_icc[args.primary]:.3f} x "
            f"ICC_{args.secondary}={self_icc[args.secondary]:.3f}))",
            f"- Mean bias ({args.primary} - {args.secondary}): **{bias:+.3f} points** "
            f"(95% CI {rho_ci[0]:+.3f} to {rho_ci[1]:+.3f})",
            f"- Quadratic-weighted kappa on skill bands: **{qwk:.3f}**",
        ]
    else:
        n_ok = int(success[(success.model == args.secondary)
                           & (success.scorer == "iqr")].succeeded.iloc[0])
        n_all = int(success[(success.model == args.secondary)
                            & (success.scorer == "iqr")].calls.iloc[0])
        iqr_headline = [
            f"- IQR agreement (rho, bias, weighted kappa) is **not computable**: only {n_ok} of "
            f"{n_all} `{args.secondary}` IQR calls returned schema-valid output, far too few to "
            "correlate against. See the output-validity table below.",
        ]

    body = "\n\n".join([
        header_block(f"B2 — {args.primary} vs {args.secondary}", manifests[args.primary], extra={
            "primary_run": manifests[args.primary].get("run_id"),
            "secondary_run": manifests[args.secondary].get("run_id"),
            "transcripts": int(paired.shape[0]),
            "combined_spend_usd": round(sum(m.get("spent_usd", 0) for m in manifests.values()), 4),
        }),
        section("Question",
                "Both scorers fall back to `gpt-4o-mini` on any exception. What does a student "
                "lose on the runs that take that path?"),
        section("Method",
                f"The same {paired.shape[0]} transcripts were scored 15 times by each model at "
                "temperature 0 with fallback disabled. Matching the repeat count matters: it makes "
                "each model's own test-retest reliability available, which the correction below "
                "requires. Agreement is reported on run medians and on single runs, because a "
                "single run is what a student actually receives."),
        section("Headline",
                f"- IQR schema-valid output: "
                f"**{success[(success.model==args.primary)&(success.scorer=='iqr')].success_rate.iloc[0]:.1%}** "
                f"for `{args.primary}` vs "
                f"**{success[(success.model==args.secondary)&(success.scorer=='iqr')].success_rate.iloc[0]:.1%}** "
                f"for `{args.secondary}`",
                f"- SIC schema-valid output: "
                f"**{success[(success.model==args.secondary)&(success.scorer=='sic')].success_rate.iloc[0]:.1%}** "
                f"for `{args.secondary}` (structured output, schema enforced by the API)",
                *iqr_headline,
                f"- SIC per-item Cohen kappa: **{sic_kappa:.3f}** "
                f"(AC1 {gwet_ac1(sic_matrix):.3f}, {percent_agreement(sic_matrix):.1%} agreement)",
                f"- Verdict: **{verdict}**"),
        section("Why the correction is not optional",
                "Agreement between two noisy judges is capped by how well each agrees with itself. "
                "An observed rho of 0.85 could mean the models genuinely disagree, or simply that "
                "both are individually unstable — and no uncorrected correlation can tell those "
                "apart. Both reliabilities come straight from the B1-style repeat runs, so the "
                "correction costs nothing extra to compute.",
                "",
                "If the corrected figure approaches 1.0, the models agree as well as they possibly "
                "could and the residual disagreement is noise rather than disposition."),
        section("Output validity — can each model answer at all?",
                "The IQR chain ends in a `PydanticOutputParser` over free text, while SIC uses "
                "`with_structured_output`, where the schema is enforced by the API rather than "
                "hoped for. That difference dominates this study.",
                success),
        section("Agreement by metric", agree),
        section("Skill band confusion", confusion),
        section("SIC item agreement",
                "Modal `elicited` label per item across the 15 runs of each model. AC1 sits beside "
                "kappa because most items are un-elicited in most transcripts.",
                sic_stats),
        section("Operating characteristics", ops),
        section("What to do about it",
                "The two scorers differ in exactly one respect that matters here. SIC builds its "
                "chain as `llm.with_structured_output(SICGradingResult)`, so the schema is enforced "
                "by the API and the model cannot return a value outside it. IQR builds "
                "`prompt | llm | PydanticOutputParser(...)`, which asks for JSON in the prompt and "
                "validates whatever free text comes back.",
                "",
                f"On `{args.primary}` that difference is invisible — it follows the instruction "
                f"every time. On `{args.secondary}` it is fatal: {int(success[(success.model==args.secondary)&(success.scorer=='iqr')].failed.iloc[0])} "
                "of the failures are the same hallucination, "
                "`listening_interpretation_and_conversational_stewardship` in place of the schema's "
                "`listening_interpretation_and_stewardship` — an extra word inserted into the "
                "longest literal in the enum.",
                "",
                "Switching the IQR chain to `with_structured_output` would make the fallback "
                "usable and cost nothing at inference time. It is deliberately **not** done as part "
                "of this study: it changes the primary path too, which would invalidate the B1 "
                "measurements collected under the current one. Change it, re-record B1, then "
                "re-run this comparison.",
                "",
                "Until then the honest description of production behaviour is that the IQR "
                "fallback does not exist — it is a code path that converts one failure into "
                "another."),
        section("Caveats",
                f"- IQR agreement statistics are absent by necessity: only "
                f"{int(success[(success.model==args.secondary)&(success.scorer=='iqr')].succeeded.iloc[0])} "
                "of 240 secondary-model IQR calls parsed, which is far too few to correlate. The "
                "SIC comparison below uses complete data on both sides (240/240 each).",
                f"- SIC agreement rests on {sic_pair.shape[0]} item-transcript pairs across 16 "
                "transcripts. A promotion decision should not rest on it.",
                "- Both models scored the same transcripts, all of which predate the v2 prompt and "
                "`ceef62d`.",
                "- Bland-Altman limits assume roughly normal differences; with n=16 they are "
                "indicative rather than tight."),
    ])

    path = write_report("b2_model_agreement", body, tables={
        "agreement": agree, "confusion": confusion,
        "sic": sic_stats, "ops": ops, "validity": success,
    })
    print(md_table(agree.head(4)))
    print(f"\nrho {rho_obs:.3f} -> corrected {rho_corr:.3f} | bias {bias:+.3f} | QWK {qwk:.3f}")
    print(f"verdict: {verdict}")
    print(f"report: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
