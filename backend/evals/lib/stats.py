"""Reliability statistics for the evaluation suite.

Absolute agreement (SEM, MDC) is separated from relative agreement (ICC) on
purpose. The golden corpus is bimodal by construction — eight interviews the
grader scored 8.2-9.0 and eight at 1.5-5.5 — so between-transcript variance is
enormous and a pooled ICC will read near 0.99 whatever the judge does. SEM and
MDC do not depend on how spread out the sample is, so they are the figures that
transfer to a new set of transcripts.
"""

from __future__ import annotations

from typing import Optional, Sequence

import numpy as np
import pandas as pd


# ── Absolute reliability ─────────────────────────────────────────────────────

def sem(long: pd.DataFrame, target: str = "transcript_id", rating: str = "score") -> float:
    """Standard error of measurement: sqrt of the within-subject mean square.

    The typical distance between one run's score and that transcript's own mean,
    in the units of the scale.
    """
    frame = long[[target, rating]].dropna()
    n_targets = frame[target].nunique()
    df_within = len(frame) - n_targets
    if df_within <= 0:
        return float("nan")
    deviations = frame[rating] - frame.groupby(target)[rating].transform("mean")
    return float(np.sqrt((deviations ** 2).sum() / df_within))


def mdc(sem_value: float, confidence: float = 0.95) -> float:
    """Minimal detectable change: how far two scorings of the same transcript
    must differ before the difference is more than measurement noise."""
    from scipy.stats import norm

    z = norm.ppf(1 - (1 - confidence) / 2)
    return float(z * np.sqrt(2) * sem_value)


def sem_and_mdc(long: pd.DataFrame, target: str = "transcript_id", rating: str = "score") -> dict:
    s = sem(long, target, rating)
    return {"sem": s, "mdc95": mdc(s), "n_targets": long[target].nunique(), "n_ratings": len(long)}


def runs_needed(sem_value: float, target_mdc: float) -> int:
    """Smallest k such that averaging k runs brings MDC95 under target_mdc.

    SEM of a k-run mean is SEM/sqrt(k), so this answers "how many times must
    production score an interview for the number to be trustworthy".
    """
    if sem_value <= 0 or target_mdc <= 0:
        return 1
    for k in range(1, 51):
        if mdc(sem_value / np.sqrt(k)) <= target_mdc:
            return k
    return 51


def icc(long: pd.DataFrame, target: str = "transcript_id",
        rater: str = "run_idx", rating: str = "score") -> pd.DataFrame:
    """Intraclass correlation. Repeat runs of one model are interchangeable, not
    distinguishable raters, so ICC1 / ICC1k (one-way random) are the rows to read."""
    import pingouin as pg

    return pg.intraclass_corr(data=long.dropna(subset=[rating]), targets=target,
                              raters=rater, ratings=rating)


def icc1(long: pd.DataFrame, target: str = "transcript_id",
         rater: str = "run_idx", rating: str = "score") -> dict:
    """ICC(1,1) and ICC(1,k) with their intervals, as plain numbers.

    pingouin labels rows "ICC(1,1)" in 0.6 and "ICC1" in 0.5, and names the
    interval column CI95 or CI95%. Pinning that shape here keeps every analysis
    script from breaking on a dependency bump.
    """
    frame = icc(long, target, rater, rating)
    types = {str(t).replace(" ", ""): t for t in frame["Type"]}
    ci_col = "CI95" if "CI95" in frame.columns else "CI95%"

    def _pick(*names: str) -> dict:
        for name in names:
            if name in types:
                row = frame[frame["Type"] == types[name]].iloc[0]
                ci = row[ci_col]
                return {
                    "icc": float(row["ICC"]),
                    "ci_low": float(ci[0]) if ci is not None else float("nan"),
                    "ci_high": float(ci[1]) if ci is not None else float("nan"),
                    "pval": float(row["pval"]),
                }
        return {"icc": float("nan"), "ci_low": float("nan"), "ci_high": float("nan"), "pval": float("nan")}

    single, average = _pick("ICC(1,1)", "ICC1"), _pick("ICC(1,k)", "ICC1k")
    return {
        "icc1_1": single["icc"], "icc1_1_ci": (single["ci_low"], single["ci_high"]),
        "icc1_k": average["icc"], "icc1_k_ci": (average["ci_low"], average["ci_high"]),
        "n_targets": long[target].nunique(), "n_raters": long[rater].nunique(),
    }


# ── Categorical agreement ────────────────────────────────────────────────────

def _rater_counts(matrix: np.ndarray, categories: Sequence) -> np.ndarray:
    """items x categories count table from an items x raters label matrix."""
    return np.array([[int((row == c).sum()) for c in categories] for row in matrix])


def percent_agreement(matrix: np.ndarray, categories: Sequence = (False, True)) -> float:
    """Mean pairwise agreement across raters, per item."""
    counts = _rater_counts(np.asarray(matrix), categories)
    r = counts.sum(axis=1)
    valid = r > 1
    if not valid.any():
        return float("nan")
    pairs = (counts * (counts - 1)).sum(axis=1)[valid] / (r[valid] * (r[valid] - 1))
    return float(pairs.mean())


def gwet_ac1(matrix: np.ndarray, categories: Sequence = (False, True)) -> float:
    """Gwet's AC1 — chance-corrected agreement that survives skewed prevalence.

    Reported next to kappa because most SIC items are un-elicited in most
    transcripts. Under that imbalance kappa collapses toward zero even at 95%
    observed agreement (the prevalence paradox), which would read as an unstable
    judge when the judge is in fact consistent.
    """
    counts = _rater_counts(np.asarray(matrix), categories)
    r = counts.sum(axis=1)
    valid = r > 1
    if not valid.any():
        return float("nan")
    counts, r = counts[valid], r[valid]

    p_a = ((counts * (counts - 1)).sum(axis=1) / (r * (r - 1))).mean()
    pi = (counts / r[:, None]).mean(axis=0)
    q = len(categories)
    p_e = float((pi * (1 - pi)).sum() / (q - 1))
    if np.isclose(p_e, 1.0):
        return float("nan")
    return float((p_a - p_e) / (1 - p_e))


def fleiss_kappa(matrix: np.ndarray, categories: Sequence = (False, True)) -> float:
    """Fleiss' kappa over an items x raters label matrix."""
    from statsmodels.stats.inter_rater import fleiss_kappa as _fk

    counts = _rater_counts(np.asarray(matrix), categories)
    counts = counts[counts.sum(axis=1) > 1]
    if len(counts) == 0:
        return float("nan")
    # An item every rater put in the same category carries no disagreement
    # information and makes the statistic undefined when *all* items are like
    # that — which is the normal case for an item nobody ever elicits.
    if (counts.max(axis=1) == counts.sum(axis=1)).all():
        return float("nan")
    return float(_fk(counts))


# ── Intervals and effect sizes ───────────────────────────────────────────────

def bootstrap_ci(values: Sequence[float], statistic=np.mean, confidence: float = 0.95,
                 n_resamples: int = 10_000, seed: int = 0) -> tuple[float, float]:
    from scipy.stats import bootstrap

    data = np.asarray([v for v in values if v is not None and not np.isnan(v)])
    if len(data) < 2:
        return (float("nan"), float("nan"))
    res = bootstrap((data,), statistic, confidence_level=confidence,
                    n_resamples=n_resamples, random_state=seed, method="BCa")
    return (float(res.confidence_interval.low), float(res.confidence_interval.high))


def cliffs_delta(a: Sequence[float], b: Sequence[float]) -> float:
    """Non-parametric effect size: P(a > b) - P(a < b)."""
    x, y = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    if len(x) == 0 or len(y) == 0:
        return float("nan")
    diff = np.sign(x[:, None] - y[None, :])
    return float(diff.sum() / (len(x) * len(y)))


def skill_band(score: float, edges: Optional[Sequence[float]] = None) -> str:
    """Numeric band for flip-rate analysis.

    SessionEvaluation.skill_label is an unconstrained str, so the judge is free
    to emit arbitrary text and label variety cannot be separated from genuine
    band movement. Banding the numeric score makes the flip rate well defined;
    label variety is reported alongside it as its own finding.
    """
    edges = edges or (3.0, 5.5, 7.5, 8.75)
    names = ("Weak", "Developing", "Proficient", "Strong", "Exemplary")
    for name, edge in zip(names, edges):
        if score < edge:
            return name
    return names[-1]
