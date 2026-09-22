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

from app.api.eval import (
    SCORER_METADATA,
    _item_display_state,
    _reconcile_moments_with_coverage,
    normalize_display_text,
    sanitize_transcript,
)
from app.evaluation.iqr_scorer import (
    IQR_DIMENSION_WEIGHTS,
    DEFAULT_PROMPT_PATH,
    compute_overall_score,
    convert_transcript_to_iqr,
)
from app.evaluation.sic_scorer import (
    DEFAULT_SIC_PROMPT_PATH,
    SIC_KEYS_DIR,
    _compute_status_for_tier,
    _quote_is_the_students,
    _student_turn_blob,
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


# ── Weighted overall score ───────────────────────────────────────────────────

class _Dim:
    """Minimal stand-in — compute_overall_score reads only these two fields."""

    def __init__(self, dimension, score):
        self.dimension = dimension
        self.score = score


def test_overall_score_is_the_weighted_formula_not_the_mean():
    """The PDF's sample session: 5.0 / 6.5 / 7.0 / 6.5 at 30/20/30/20 -> 6.2.

    The mock's header shows 6.5, which predates the formula. 6.2 sits just
    BELOW the simple mean of 6.25 because Framing is both the weakest dimension
    here and the most heavily weighted — which is the weighting working, not a
    bug. No weighting that up-weights Framing and Follow-Up Depth reaches 6.5 on
    this session.
    """
    dims = [
        _Dim("framing_and_stakeholder_fit", 5.0),
        _Dim("question_quality_and_precision", 6.5),
        _Dim("probing_and_follow_up_depth", 7.0),
        _Dim("listening_interpretation_and_stewardship", 6.5),
    ]
    assert compute_overall_score(dims) == pytest.approx(6.2)
    # A simple mean would give 6.25 — the weighting has to be visible in the
    # output, or the formula is decorative.
    assert compute_overall_score(dims) != pytest.approx(6.25)


def test_weights_favour_framing_and_probing_and_sum_to_one():
    assert sum(IQR_DIMENSION_WEIGHTS.values()) == pytest.approx(1.0)
    assert (
        IQR_DIMENSION_WEIGHTS["probing_and_follow_up_depth"]
        > IQR_DIMENSION_WEIGHTS["question_quality_and_precision"]
    )
    assert (
        IQR_DIMENSION_WEIGHTS["framing_and_stakeholder_fit"]
        > IQR_DIMENSION_WEIGHTS["question_quality_and_precision"]
    )


def test_overall_score_renormalises_over_present_dimensions():
    """A response missing a dimension must not silently score lower."""
    partial = [_Dim("framing_and_stakeholder_fit", 8.0),
               _Dim("probing_and_follow_up_depth", 8.0)]
    assert compute_overall_score(partial) == pytest.approx(8.0)
    assert compute_overall_score([]) is None


# ── SIC key authored content ─────────────────────────────────────────────────

@pytest.mark.parametrize("persona_id", sorted(EXPECTED_CATALOG_SIZES))
def test_every_catalog_item_has_a_unique_display_label(persona_id):
    """Labels are static and name the item in three places in the report — the
    box, the moments' out_of_reach_item, and the consequence line. A duplicate
    makes the moment reconciliation ambiguous."""
    data = json.loads((SIC_KEYS_DIR / f"{persona_id}_sic_key.json").read_text(encoding="utf-8"))
    labels = [i.get("display_label") for i in data["sic_catalog"]]
    assert all(labels), f"{persona_id} has items without a display_label"
    assert len(set(labels)) == len(labels), f"{persona_id} has duplicate display_labels"


@pytest.mark.parametrize("persona_id", sorted(EXPECTED_CATALOG_SIZES))
def test_all_personas_gate_tier_2(persona_id):
    """Alex was the only persona without the gate, which broke cross-persona
    comparability — the whole point of grading four personas on one scale."""
    data = json.loads((SIC_KEYS_DIR / f"{persona_id}_sic_key.json").read_text(encoding="utf-8"))
    assert data["grading_rules"]["tier_2_requires_gate"] is True


@pytest.mark.parametrize("persona_id", sorted(EXPECTED_CATALOG_SIZES))
def test_tier_metadata_is_authored_for_every_tier(persona_id):
    data = json.loads((SIC_KEYS_DIR / f"{persona_id}_sic_key.json").read_text(encoding="utf-8"))
    for tier in ("1", "2", "3"):
        meta = data["tier_metadata"][tier]
        assert meta["title"].strip(), f"{persona_id} tier {tier} has no title"
        assert meta["description"].strip(), f"{persona_id} tier {tier} has no description"
    # Tier 3's description has to explain what it covers without the word
    # "signal", which means nothing to a student.
    assert "signal" not in data["tier_metadata"]["3"]["description"].lower()


# ── Display normalisation and moment reconciliation ──────────────────────────

def test_proper_nouns_are_fixed_for_display_only():
    assert normalize_display_text("we studied Harvard Town") == "we studied Harbortown"
    assert normalize_display_text("Harbor Town flooding") == "Harbortown flooding"
    assert (
        normalize_display_text("I'm at the University of at the Worcester Polytechnic Institute")
        == "I'm at Worcester Polytechnic Institute"
    )
    # Correct text is left alone, and empty input round-trips.
    assert normalize_display_text("Harbortown") == "Harbortown"
    assert normalize_display_text("") == ""
    assert normalize_display_text(None) is None


def test_item_display_state_treats_appropriate_restraint_as_opened():
    """Restraint in response to good framing is the student's move working, not
    a miss — it rendered as a blank square before."""
    earned = {"elicited": True, "earned_mode": "earned", "credit_mode": "explicit"}
    indirect = {"elicited": True, "earned_mode": "earned", "credit_mode": "indirect_acknowledgment"}
    restraint = {"elicited": False, "type": "signal",
                 "omission_classification": "appropriate_non_disclosure"}
    volunteered = {"elicited": False, "earned_mode": "volunteered"}
    assert _item_display_state(earned) == "earned"
    assert _item_display_state(indirect) == "opened"
    assert _item_display_state(restraint) == "opened"
    assert _item_display_state(volunteered) == "not_opened"


def test_moment_item_reference_resolves_against_real_coverage():
    payload = {
        "moments": [
            {"out_of_reach_item": "Displacement Stress"},
            {"out_of_reach_item": "Nonexistent Item"},
            {"out_of_reach_item": None},
        ],
        "insight_coverage": [
            {"items": [{
                "display_label": "Displacement Stress",
                "elicited": False,
                "type": "signal",
                "omission_classification": "insufficient_framing",
            }]},
        ],
    }
    _reconcile_moments_with_coverage(payload)
    assert payload["moments"][0]["out_of_reach_state"] == "not_opened"
    # An invented label is dropped rather than rendered against a tab that has
    # never heard of it.
    assert payload["moments"][1]["out_of_reach_item"] is None
    assert "out_of_reach_state" not in payload["moments"][1]
    assert "out_of_reach_state" not in payload["moments"][2]


# ── Evidence quotes belong to the student ────────────────────────────────────

_TURNS = [
    {"role": "user", "text": "Could you tell me about your day to day role?"},
    {"role": "assistant",
     "text": "One of the challenges is balancing immediate priorities, like maintaining "
             "our infrastructure, with longer-term adaptation strategies."},
    {"role": "user", "text": "What makes that balance hard to hold?"},
]


def test_persona_lines_are_not_credited_as_the_students_words():
    """The report labels this quote "How you accessed it". A persona line there
    tells the student they said something they never said."""
    blob = _student_turn_blob(_TURNS)
    assert _quote_is_the_students("Could you tell me about your day to day role?", blob)
    # Trimming and re-punctuation by the grader still matches.
    assert _quote_is_the_students("could you tell me about your day to day role", blob)
    assert _quote_is_the_students("\u201cWhat makes that balance hard to hold?\u201d", blob)
    # The persona's own line does not.
    assert not _quote_is_the_students(
        "One of the challenges is balancing immediate priorities, like maintaining "
        "our infrastructure, with longer-term adaptation strategies.",
        blob,
    )


def test_quote_check_does_not_discard_when_speakers_are_unlabelled():
    """No identifiable student turns means we cannot judge — dropping every
    quote there would be worse than keeping them."""
    assert _quote_is_the_students("anything at all", "")
    assert not _quote_is_the_students("", _student_turn_blob(_TURNS))


def test_student_blob_reads_speaker_or_role():
    assert "day to day role" in _student_turn_blob(_TURNS)
    assert "our infrastructure" not in _student_turn_blob(_TURNS)
    # The IQR-style transcript labels the speaker instead of the role.
    assert "hello there" in _student_turn_blob([{"speaker": "Student", "text": "Hello there"}])
