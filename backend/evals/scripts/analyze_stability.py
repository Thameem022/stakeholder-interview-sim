"""B1 analysis — reads a collection run's JSONL and writes the report.

Costs nothing and touches no network, so it can be re-run as often as the cuts
need changing. Absolute reliability (SEM, MDC95) leads; ICC is reported pooled
and within stratum, because the golden corpus is bimodal by construction and the
pooled figure flatters the judge.

    uv run --group evals python -m evals.scripts.analyze_stability [--run DIR]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from app.evaluation.sic_scorer import SIC_KEYS_DIR, _compute_status_for_tier
from evals.lib.report import (
    header_block,
    latest_run,
    load_records,
    md_table,
    read_manifest,
    section,
    write_report,
)
from evals.lib.stats import (
    bootstrap_ci,
    gwet_ac1,
    fleiss_kappa,
    icc1,
    percent_agreement,
    runs_needed,
    sem_and_mdc,
    skill_band,
)

DIMENSIONS = [
    "framing_and_stakeholder_fit",
    "question_quality_and_precision",
    "probing_and_follow_up_depth",
    "listening_interpretation_and_stewardship",
]


# ── Reshaping ────────────────────────────────────────────────────────────────

def iqr_long(records: pd.DataFrame) -> pd.DataFrame:
    """One row per (transcript, run, metric). 'overall' is a metric like any other."""
    rows = []
    for r in records[records.kind == "iqr"].itertuples():
        if not r.result:
            continue
        base = {
            "transcript_id": r.transcript_id, "run_idx": r.run_idx,
            "persona_id": r.persona_id, "quality": r.quality,
            "prompt_era": r.prompt_era, "turn_count": r.turn_count,
            "human_overall": r.human_overall,
        }
        rows.append({**base, "metric": "overall", "score": float(r.result["overall_score"]),
                     "skill_label": r.result["skill_label"]})
        for d in r.result["dimensions"]:
            rows.append({**base, "metric": d["dimension"], "score": float(d["score"]),
                         "skill_label": None})
    return pd.DataFrame(rows)


def _catalog(persona_id: str) -> dict:
    data = json.loads((SIC_KEYS_DIR / f"{persona_id}_sic_key.json").read_text(encoding="utf-8"))
    return {i["chunk_id"]: i for i in data.get("sic_catalog", [])}


def sic_item_long(records: pd.DataFrame) -> pd.DataFrame:
    """One row per (transcript, run, SIC item)."""
    catalogs = {p: _catalog(p) for p in records.persona_id.unique()}
    rows = []
    for r in records[records.kind == "sic"].itertuples():
        if not r.result:
            continue
        cat = catalogs[r.persona_id]
        for g in r.result["grades"]:
            item = cat.get(g["chunk_id"])
            if item is None:
                continue  # a chunk_id the grader invented; counted as a validity defect below
            rows.append({
                "transcript_id": r.transcript_id, "run_idx": r.run_idx,
                "persona_id": r.persona_id, "quality": r.quality,
                "chunk_id": g["chunk_id"], "tier": int(item.get("tier", 0)),
                "type": item.get("type", "fact"),
                "elicited": bool(g["elicited"]), "earned_mode": g.get("earned_mode"),
                "credited": bool(g["elicited"]) and g.get("earned_mode") == "earned",
            })
    return pd.DataFrame(rows)


def sic_tier_pct(item_long: pd.DataFrame) -> pd.DataFrame:
    """Re-derive tier percentages with the scorer's own arithmetic.

    Tier percentage is a deterministic function of the item grades, so all of
    its variance must trace back to the LLM labels — a useful cross-check that
    the pipeline is not adding noise of its own.
    """
    rows = []
    for (tid, run, tier), grp in item_long.groupby(["transcript_id", "run_idx", "tier"]):
        views = [{"elicited": r.elicited, "earned_mode": r.earned_mode,
                  "credit_mode": "explicit_acknowledgment" if r.credited else None,
                  "omission_classification": None} for r in grp.itertuples()]
        _, pct, _ = _compute_status_for_tier(int(tier), views)
        rows.append({"transcript_id": tid, "run_idx": run, "tier": int(tier), "pct": pct})
    return pd.DataFrame(rows)


# ── Analyses ─────────────────────────────────────────────────────────────────

def reliability_table(long: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for metric, grp in long.groupby("metric"):
        stats = sem_and_mdc(grp)
        spread = grp.groupby("transcript_id").score.agg(["std", lambda s: s.max() - s.min()])
        spread.columns = ["sd", "range"]
        rows.append({
            "metric": metric, "sem": stats["sem"], "mdc95": stats["mdc95"],
            "mean_within_sd": spread.sd.mean(), "max_within_sd": spread.sd.max(),
            "mean_range": spread["range"].mean(), "max_range": spread["range"].max(),
            "k_for_mdc_1pt": runs_needed(stats["sem"], 1.0),
            "n": stats["n_ratings"],
        })
    order = ["overall"] + DIMENSIONS
    frame = pd.DataFrame(rows)
    return frame.set_index("metric").reindex([m for m in order if m in set(frame.metric)]).reset_index()


def variance_components(long: pd.DataFrame) -> pd.DataFrame:
    """Separate measurement noise from true between-transcript spread, per stratum.

    A correlation-style coefficient answers "how much of the observed variance is
    real", which on a corpus built from eight strong and eight weak interviews is
    mostly a statement about the corpus. This asks the question that survives a
    change of sample: how large is the real spread between comparable interviews
    compared with the noise in measuring one of them.
    """
    rows = []
    for metric, quality in [(m, q) for m in long.metric.unique()
                            for q in sorted(long.quality.unique())]:
        d = long[(long.metric == metric) & (long.quality == quality)]
        k = d.transcript_id.nunique()
        if k < 2:
            continue
        runs = d.groupby("transcript_id").size().mean()
        means = d.groupby("transcript_id").score.mean()
        ms_between = (runs * (means - d.score.mean()) ** 2).sum() / (k - 1)
        ms_within = ((d.score - d.groupby("transcript_id").score.transform("mean")) ** 2).sum() / (len(d) - k)
        # A negative variance estimate means the data carry no detectable
        # between-transcript spread at all; it is clamped to zero, not reported
        # as a small negative number.
        true_sd = float(np.sqrt(max((ms_between - ms_within) / runs, 0.0)))
        sem_ = float(np.sqrt(ms_within))
        rows.append({
            "metric": metric, "stratum": quality, "n_transcripts": k,
            "sem": sem_, "true_between_sd": true_sd,
            "spread_over_noise": (true_sd / sem_) if sem_ else float("nan"),
            "distinct_means": int(means.round(2).nunique()),
        })
    return pd.DataFrame(rows).sort_values(["stratum", "spread_over_noise"])


def icc_table(long: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for metric, grp in long.groupby("metric"):
        for label, subset in (("pooled", grp),
                              ("good only", grp[grp.quality == "good"]),
                              ("bad only", grp[grp.quality == "bad"])):
            if subset.transcript_id.nunique() < 2:
                continue
            try:
                res = icc1(subset)
            except Exception:
                continue
            rows.append({
                "metric": metric, "stratum": label, "n_transcripts": res["n_targets"],
                "icc_1_1": res["icc1_1"], "ci_low": res["icc1_1_ci"][0], "ci_high": res["icc1_1_ci"][1],
                "icc_1_k": res["icc1_k"],
            })
    return pd.DataFrame(rows)


def band_flips(long: pd.DataFrame) -> pd.DataFrame:
    overall = long[long.metric == "overall"]
    rows = []
    for tid, grp in overall.groupby("transcript_id"):
        bands = grp.score.map(skill_band)
        rows.append({
            "transcript_id": tid, "quality": grp.quality.iloc[0],
            "mean_score": grp.score.mean(), "sd": grp.score.std(),
            "n_bands": bands.nunique(), "bands": "/".join(sorted(bands.unique())),
            "n_raw_labels": grp.skill_label.nunique(),
            "raw_labels": " | ".join(sorted(set(grp.skill_label.dropna()))[:3]),
        })
    return pd.DataFrame(rows).sort_values("sd", ascending=False)


def sic_agreement(item_long: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Per-item stability of the binary `elicited` label across repeat runs."""
    per_item = []
    for (tid, chunk), grp in item_long.groupby(["transcript_id", "chunk_id"]):
        labels = grp.sort_values("run_idx").elicited.to_numpy()
        n_true = int(labels.sum())
        per_item.append({
            "transcript_id": tid, "chunk_id": chunk, "tier": int(grp.tier.iloc[0]),
            "type": grp.type.iloc[0], "quality": grp.quality.iloc[0],
            "n_runs": len(labels), "n_elicited": n_true,
            "disagreement": min(n_true, len(labels) - n_true) / len(labels),
        })
    items = pd.DataFrame(per_item)

    rows = []
    for label, subset in [("all", item_long), *[(f"tier {t}", item_long[item_long.tier == t])
                                                for t in sorted(item_long.tier.unique())]]:
        matrix = (subset.pivot_table(index=["transcript_id", "chunk_id"], columns="run_idx",
                                     values="elicited", aggfunc="first")
                  .dropna().astype(bool).to_numpy())
        if len(matrix) == 0:
            continue
        rows.append({
            "scope": label, "n_item_transcripts": len(matrix),
            "pct_agreement": percent_agreement(matrix),
            "fleiss_kappa": fleiss_kappa(matrix), "gwet_ac1": gwet_ac1(matrix),
            "pct_unstable": float((items[items.chunk_id.isin(
                subset.chunk_id.unique())].disagreement > 0.2).mean()),
        })
    return pd.DataFrame(rows), items.sort_values("disagreement", ascending=False)


def main() -> int:
    parser = argparse.ArgumentParser(description="B1 stability analysis")
    parser.add_argument("--run", type=Path, default=None, help="run directory (default: latest)")
    parser.add_argument("--name", default="b1_stability")
    # Defaulting to None would resolve to whichever run finished most recently —
    # after a B2 collection that is the secondary model, and the analysis would
    # silently describe a different study under this one's name.
    parser.add_argument("--model", default="gpt-4o",
                        help="model whose run to analyse; pass '' to take the latest of any model")
    args = parser.parse_args()

    run_dir = args.run or latest_run(args.name, args.model or None)
    manifest = read_manifest(run_dir)
    records = load_records(run_dir)

    resolved = manifest.get("config", {}).get("model", "unknown")
    print(f"analysing {run_dir.name}\n  model={resolved}  records={len(records)}")
    parsed = records[(records.kind == "iqr") & records.error.isna()]
    if len(parsed) < 0.9 * (records.kind == "iqr").sum():
        print(f"  WARNING: only {len(parsed)}/{(records.kind == 'iqr').sum()} IQR calls parsed — "
              "reliability figures below are computed on that subset")

    long = iqr_long(records)
    items = sic_item_long(records)
    tiers = sic_tier_pct(items)

    rel = reliability_table(long)
    components = variance_components(long)
    iccs = icc_table(long)
    flips = band_flips(long)
    agree, per_item = sic_agreement(items)

    overall = long[long.metric == "overall"]
    sem_overall = sem_and_mdc(overall)
    n_runs = int(records.run_idx.max()) + 1

    # Tier percentage spread, per tier.
    tier_sd = (tiers.groupby(["transcript_id", "tier"]).pct.std().reset_index()
               .groupby("tier").pct.agg(["mean", "max"]).reset_index()
               .rename(columns={"mean": "mean_within_sd", "max": "max_within_sd"}))

    # Operational health.
    errors = int(records.error.notna().sum()) if "error" in records else 0
    fallbacks = int((records.model_used != manifest.get("config", {}).get("model")).sum())
    fingerprints = sorted(set(records.system_fingerprint.dropna()))

    # Does instability concentrate anywhere?
    per_transcript = (overall.groupby("transcript_id")
                      .agg(sd=("score", "std"), mean=("score", "mean"),
                           turns=("turn_count", "first"), quality=("quality", "first"))
                      .reset_index())
    corr_len = per_transcript[["sd", "turns"]].corr().iloc[0, 1]
    corr_mean = per_transcript[["sd", "mean"]].corr().iloc[0, 1]
    sd_ci = bootstrap_ci(per_transcript.sd.dropna().tolist())

    median_score = per_transcript["mean"].median()
    weak_half = per_transcript[per_transcript["mean"] <= median_score]
    strong_half = per_transcript[per_transcript["mean"] > median_score]
    gap = weak_half.sd.mean() - strong_half.sd.mean()
    if abs(gap) < 0.02:
        concentration_note = (
            "Instability is spread evenly across the score range; no group of interviews is "
            "measurably harder for the judge to score consistently than another."
        )
    elif gap > 0:
        concentration_note = (
            "The judge is markedly **less** consistent on the weaker interviews than the stronger "
            "ones. That is the benign direction: a strong interview is graded the same way every "
            "time, and the residual movement sits where scores are low enough that a fraction of a "
            "point does not change what the student is told. It would be the worrying direction if "
            "the spread sat near a band boundary instead."
        )
    else:
        concentration_note = (
            "The judge is less consistent on the **stronger** interviews. That is the direction "
            "worth watching, since those scores sit near the top band boundaries where a fraction "
            "of a point changes the label a student sees."
        )

    k_needed = runs_needed(sem_overall["sem"], 1.0)
    verdict = (
        "single-run scoring stands" if sem_overall["mdc95"] <= 1.0
        else f"ship k-run median scoring (k={k_needed} brings MDC95 under 1.0 point)"
    )

    body = "\n\n".join([
        header_block("B1 — Repeat-scoring stability", manifest, extra={
            "transcripts": long.transcript_id.nunique(),
            "repeats_per_transcript": n_runs,
            "iqr_judgements": int((records.kind == "iqr").sum()),
            "sic_judgements": int((records.kind == "sic").sum()),
        }),
        section("Question",
                "How far does the same transcript's score move between identical scoring runs, "
                "and is a single run enough to grade a student on?"),
        section("Method",
                f"Each of the {long.transcript_id.nunique()} graded transcripts was scored "
                f"{n_runs} times by `{manifest.get('config', {}).get('model')}` at temperature 0 with "
                "the fallback to gpt-4o-mini disabled, so every judgement is attributable to the "
                "primary model. Transcripts were sanitized once before both scorers, exactly as the "
                "production endpoint does. SIC went through `grade_raw`, which returns per-item "
                "labels and skips the cosmetic enrichment call.",
                "",
                "Absolute reliability leads because it does not depend on how spread out this "
                "sample happens to be. SEM is the within-subject root mean square from a one-way "
                "ANOVA; MDC95 = 1.96 x sqrt(2) x SEM is how far two scorings of one transcript must "
                "differ before the difference is more than noise."),
        section("Headline",
                f"- **SEM {sem_overall['sem']:.3f} points** on the 10-point overall score",
                f"- **MDC95 {sem_overall['mdc95']:.3f} points** — two runs on the same transcript "
                f"differ by more than this 5% of the time by chance alone",
                f"- Mean within-transcript SD {per_transcript.sd.mean():.3f} "
                f"(95% CI {sd_ci[0]:.3f}-{sd_ci[1]:.3f}), max {per_transcript.sd.max():.3f}",
                f"- Band flip rate **{(flips.n_bands > 1).mean():.1%}** of transcripts "
                f"({int((flips.n_bands > 1).sum())}/{len(flips)}) landed in more than one skill band",
                f"- Production decision: **{verdict}**"),
        section("Absolute reliability by metric", rel),
        section("Discrimination — real spread against measurement noise",
                "For each stratum: the SEM of a single scoring, and the estimated standard "
                "deviation of the *true* scores across comparable interviews. The ratio is what "
                "matters — a metric whose true spread is smaller than its own noise cannot rank "
                "one interview above another, however consistent it is.",
                "",
                "`distinct_means` counts how many different average scores the metric actually "
                "produced across the transcripts in that stratum. A value of 1 or 2 means the "
                "metric is at a ceiling or floor and is not measuring anything within that group.",
                components),
        section("ICC — pooled and within stratum",
                "The corpus splits into eight interviews the grader scored 8.2-9.0 and eight at "
                "1.5-5.5, so between-transcript variance is enormous and the pooled ICC mostly "
                "confirms the judge can tell good from bad. The within-stratum rows are the ones "
                "that describe behaviour among comparable interviews; with n=8 their intervals "
                "are wide, and that is stated rather than hidden.",
                iccs),
        section("Band stability per transcript",
                "`skill_label` is an unconstrained string in the schema, so raw label variety is "
                "reported separately from movement between numeric bands.",
                flips),
        section("SIC per-item agreement",
                "Kappa and AC1 are shown together because most items are un-elicited in most "
                "transcripts. Under that prevalence imbalance kappa collapses toward zero even at "
                "very high observed agreement, which would read as an unstable judge when it is "
                "not; AC1 is the prevalence-robust counterpart.",
                agree),
        section("Least stable SIC items",
                "Items whose `elicited` label disagreed across runs most often — direct input for "
                "prompt revision.",
                per_item.head(15)),
        section("Tier percentage spread",
                "Tier arithmetic is deterministic given the item grades, so this variance traces "
                "entirely back to the LLM labels above.",
                tier_sd),
        section("Where instability concentrates",
                f"- Within-transcript SD vs transcript length: r = {corr_len:.3f}",
                f"- Within-transcript SD vs mean score: r = {corr_mean:.3f}",
                f"- Mean SD among the {len(weak_half)} lowest-scoring transcripts: "
                f"{weak_half.sd.mean():.3f}",
                f"- Mean SD among the {len(strong_half)} highest-scoring transcripts: "
                f"{strong_half.sd.mean():.3f}",
                f"- Transcripts identical across all {n_runs} runs (SD = 0): "
                f"{int((per_transcript.sd == 0).sum())} of {len(per_transcript)}",
                "",
                concentration_note,
                per_transcript.sort_values("sd", ascending=False)),
        section("Operational health",
                f"- Errors: {errors} of {len(records)} calls",
                f"- Silent fallbacks to the secondary model: {fallbacks} (strict mode should make this 0)",
                f"- `system_fingerprint` values seen: {len(fingerprints)} — {', '.join(fingerprints) or 'none reported'}",
                f"- Spend: ${manifest.get('spent_usd', 0):.4f} over {manifest.get('api_calls')} API calls",
                f"- Median call latency: {records.latency_ms.median():.0f} ms "
                f"(p95 {records.latency_ms.quantile(0.95):.0f} ms)",
                "",
                "More than one fingerprint means part of the measured spread is OpenAI's serving "
                "backend changing under the run rather than the judge itself."),
        section("Caveats",
                "- All 16 transcripts predate commit `ceef62d` and the v2 prompt (`51f2e82`), so "
                "they were produced by an older persona build. This bounds how far the figure "
                "generalises to interviews students run today; it does not affect the measurement "
                "of the current judge's self-consistency, which is what B1 claims.",
                "- Within-stratum ICC rests on n=8 transcripts per stratum.",
                "- Temperature 0 is not determinism; that is the premise of the study, not a flaw "
                "in it."),
    ])

    path = write_report("b1_stability", body, tables={
        "reliability": rel, "icc": iccs, "band_flips": flips,
        "sic_agreement": agree, "sic_items": per_item, "variance_components": components,
        "tier_sd": tier_sd, "per_transcript": per_transcript,
    })
    print(md_table(rel))
    print(f"\nSEM {sem_overall['sem']:.3f} | MDC95 {sem_overall['mdc95']:.3f} | verdict: {verdict}")
    print(f"report: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
