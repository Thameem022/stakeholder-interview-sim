"""Unit tests for the evaluation harness. No API key, no network, no database.

These pin the contracts the B-tier studies depend on: if the transcript
conversion, the tier arithmetic or the cache key changes shape, a collected run
silently stops meaning what its report says it means.
"""

from __future__ import annotations

import json
import re

import numpy as np
import pytest
from scipy.stats import norm

from app.api.eval import SCORER_METADATA, sanitize_transcript
from app.evaluation.iqr_scorer import DEFAULT_PROMPT_PATH, convert_transcript_to_iqr
from app.evaluation.sic_scorer import (
    DEFAULT_SIC_PROMPT_PATH,
    SIC_KEYS_DIR,
    _compute_status_for_tier,
)

EXPECTED_CATALOG_SIZES = {
    "alex_martinez": 15,
    "michael_mike_alvarez": 11,
    "sarah_donnelly": 12,
    "thomas_tom_caldwell": 12,
}


# ── Transcript handling ──────────────────────────────────────────────────────

def test_convert_transcript_maps_roles_and_numbers_turns():
    raw = {
        "turns": [
            {"role": "user", "text": "Tell me about the flooding."},
            {"role": "assistant", "text": "It has been getting worse."},
        ],
        "persona_key": "sarah_donnelly",
        "session_id": "s1",
    }
    transcript = convert_transcript_to_iqr(raw)
    assert [t.turn_id for t in transcript.turns] == [1, 2]
    assert transcript.turns[0].speaker == "Student"
    assert transcript.turns[1].speaker == "Sarah Donnelly"
    assert transcript.metadata["persona_key"] == "sarah_donnelly"
    assert transcript.metadata["session_id"] == "s1"


def test_sanitize_drops_bare_artifacts_but_keeps_real_content():
    turns = [
        {"role": "user", "text": "bye"},
        {"role": "user", "text": "Bye, that was helpful"},
        {"role": "assistant", "text": "um"},
        {"role": "user", "text": "  "},
        {"role": "user", "text": "What changed after the 2023 storm?"},
    ]
    kept = [t["text"] for t in sanitize_transcript(turns)]
    assert "bye" not in kept and "um" not in kept
    assert "Bye, that was helpful" in kept
    assert "What changed after the 2023 storm?" in kept


# ── Tier arithmetic ──────────────────────────────────────────────────────────

def _view(elicited, earned="earned", credit=None, omission=None):
    return {"elicited": elicited, "earned_mode": earned,
            "credit_mode": credit, "omission_classification": omission}


def test_tier3_credit_weights():
    """explicit 1.0 + indirect 0.7 + reflective 0.5 + miss = 2.2/4 = 55%."""
    views = [
        _view(True, credit="explicit_acknowledgment"),
        _view(True, credit="indirect_acknowledgment"),
        _view(True, credit="reflective_silence"),
        _view(False, earned="not_present"),
    ]
    status, pct, label = _compute_status_for_tier(3, views)
    assert pct == pytest.approx(55.0)
    assert status == "partial" and label == "Proficient"


def test_volunteered_content_earns_no_credit():
    """The earned_mode gate is what stops a persona that spilled content
    unprompted from inflating a student's coverage."""
    views = [_view(True, earned="volunteered", credit="explicit_acknowledgment")] * 4
    status, pct, _ = _compute_status_for_tier(3, views)
    assert pct == 0.0
    assert status.startswith("not_accessed")


def test_zero_elicited_tier3_uses_majority_omission_classification():
    restrained = [_view(False, "not_present", omission="appropriate_non_disclosure")] * 3
    restrained += [_view(False, "not_present", omission="insufficient_framing")]
    assert _compute_status_for_tier(3, restrained)[0] == "not_accessed_appropriate_restraint"

    # Unclassified omissions default to the developmental reading, not praise.
    unclassified = [_view(False, "not_present")] * 4
    assert _compute_status_for_tier(3, unclassified)[0] == "not_accessed_insufficient_framing"


def test_tier1_is_a_binary_count():
    views = [_view(True), _view(True), _view(False, "not_present"), _view(False, "not_present")]
    status, pct, label = _compute_status_for_tier(1, views)
    assert pct == pytest.approx(50.0) and status == "partial" and label == "Proficient"
    assert _compute_status_for_tier(1, [])[1] == 0.0


# ── SIC keys ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("persona_id,size", sorted(EXPECTED_CATALOG_SIZES.items()))
def test_sic_catalog_sizes_and_unique_ids(persona_id, size):
    data = json.loads((SIC_KEYS_DIR / f"{persona_id}_sic_key.json").read_text(encoding="utf-8"))
    catalog = data["sic_catalog"]
    assert len(catalog) == size
    ids = [i["chunk_id"] for i in catalog]
    assert len(set(ids)) == len(ids), "duplicate chunk_id would collide in grades_by_id"
    assert {int(i["tier"]) for i in catalog} == {1, 2, 3}


def test_default_prompts_live_in_version_directories():
    """`IQRScorer.prompt_version` reads the parent directory name, and the eval
    endpoint persists that value per run. Both only mean anything while the
    prompts actually sit in a versioned directory."""
    for path in (DEFAULT_PROMPT_PATH, DEFAULT_SIC_PROMPT_PATH):
        assert path.is_file(), path
        assert re.fullmatch(r"v\d+", path.parent.name), path.parent.name


def test_scorer_metadata_records_git_sha():
    assert "git_sha" in SCORER_METADATA


# ── Golden dataset ───────────────────────────────────────────────────────────

def test_golden_dataset_loads_and_validates():
    from evals.lib.golden import load_golden

    frame = load_golden()
    assert len(frame) == 16
    assert set(frame.persona_id) == set(EXPECTED_CATALOG_SIZES)
    assert frame.turn_count.sum() == 493
    # Balanced design: every persona contributes one good and one bad interview
    # in each of the two prompt eras.
    assert (frame.groupby(["persona_id", "quality", "prompt_era"]).size() == 1).all()


def test_golden_turns_survive_transcript_conversion():
    from evals.lib.golden import load_golden

    for row in load_golden().itertuples():
        transcript = convert_transcript_to_iqr(
            {"turns": sanitize_transcript(row.turns), "persona_key": row.persona_id}
        )
        assert transcript.turns, row.transcript_id
        assert all(t.text.strip() for t in transcript.turns)


# ── Cache identity ───────────────────────────────────────────────────────────

def test_cache_key_is_order_independent_but_content_sensitive():
    from evals.lib.cache import call_key

    base = dict(scorer="iqr", model="gpt-4o", run_idx=0, transcript_sha="abc")
    assert call_key(**base) == call_key(**dict(reversed(list(base.items()))))
    assert call_key(**base) != call_key(**{**base, "run_idx": 1})
    assert call_key(**base) != call_key(**{**base, "model": "gpt-4o-mini"})
    # An in-place prompt edit must invalidate, or the run silently reuses
    # results from before the edit.
    assert call_key(**base, prompt_sha="v1") != call_key(**base, prompt_sha="v2")


def test_cache_round_trip_and_atomic_write(tmp_path):
    from evals.lib.cache import CallCache

    cache = CallCache(tmp_path)
    assert cache.get("deadbeef") is None
    cache.put("deadbeef", {"result": 1})
    assert cache.get("deadbeef") == {"result": 1}
    assert cache.stats == {"hits": 1, "misses": 1}
    assert not list(tmp_path.rglob("*.tmp")), "temp files must be renamed, not left behind"


# ── Statistics ───────────────────────────────────────────────────────────────

def test_sem_and_mdc_against_hand_computed_values():
    import pandas as pd

    from evals.lib.stats import mdc, sem_and_mdc

    # 3 targets x 4 runs, every deviation exactly +/-1 -> SS_within = 12, df = 9.
    rows = [{"transcript_id": f"t{t}", "run_idx": r, "score": base + d}
            for t, base in enumerate([2.0, 5.0, 8.0])
            for r, d in enumerate([-1.0, 1.0, -1.0, 1.0])]
    result = sem_and_mdc(pd.DataFrame(rows))
    assert result["sem"] == pytest.approx(np.sqrt(12 / 9))
    # z from the exact normal quantile, not the rounded 1.96.
    z = norm.ppf(0.975)
    assert result["mdc95"] == pytest.approx(z * np.sqrt(2) * result["sem"])
    assert result["mdc95"] == pytest.approx(2.77 * result["sem"], rel=1e-3)
    assert mdc(0.0) == pytest.approx(0.0)


def test_runs_needed_shrinks_with_averaging():
    from evals.lib.stats import runs_needed

    assert runs_needed(0.20, 1.0) == 1
    assert runs_needed(1.0, 1.0) > 1
    # SEM of a k-run mean is SEM/sqrt(k), so a noisier judge needs more runs.
    assert runs_needed(1.5, 1.0) >= runs_needed(1.0, 1.0)


def test_ac1_survives_the_prevalence_paradox():
    from evals.lib.stats import fleiss_kappa, gwet_ac1, percent_agreement

    matrix = np.zeros((20, 5), dtype=bool)
    matrix[0] = [True, True, True, False, False]
    assert percent_agreement(matrix) > 0.95
    # Kappa collapses under skewed prevalence; AC1 does not. Reporting only
    # kappa would read as an unstable judge when the judge is consistent.
    assert fleiss_kappa(matrix) < 0.6
    assert gwet_ac1(matrix) > 0.9


def test_skill_bands_cover_the_observed_range():
    from evals.lib.stats import skill_band

    assert skill_band(1.5) == "Weak"
    assert skill_band(9.0) == "Exemplary"
    # Monotonic: a higher score never lands in a lower band.
    names = [skill_band(s) for s in np.arange(1.0, 10.01, 0.25)]
    order = ["Weak", "Developing", "Proficient", "Strong", "Exemplary"]
    assert [order.index(n) for n in names] == sorted(order.index(n) for n in names)


def test_cliffs_delta_endpoints():
    from evals.lib.stats import cliffs_delta

    assert cliffs_delta([5, 6, 7], [1, 2, 3]) == pytest.approx(1.0)
    assert cliffs_delta([1, 2, 3], [5, 6, 7]) == pytest.approx(-1.0)
    assert cliffs_delta([1, 2, 3], [1, 2, 3]) == pytest.approx(0.0)
