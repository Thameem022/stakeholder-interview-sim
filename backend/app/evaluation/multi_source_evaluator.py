from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np


_SENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")

WORLD_KEYWORDS = {
    "harbortown", "town", "waterfront", "flood", "infrastructure", "council", "department",
    "planning", "zoning", "resilience", "coastal", "policy", "adaptation", "marsh", "sea level",
}

PERSONA_FACT_CUES = {
    "i am", "my name", "my role", "i work", "i've worked", "i have worked", "my department",
    "i live", "i grew up", "my family", "my education", "years", "experience",
}

BEHAVIORAL_CUES = {
    "at this stage", "based on our current understanding", "one of the challenges",
    "trade-offs", "feasibility", "capacity", "sequencing", "constraints", "stakeholder process",
}

ASSISTANT_LEAKAGE_PHRASES = [
    "here are three options",
    "i recommend",
    "in conclusion",
    "as an ai",
    "let me provide",
]

ABSOLUTE_TERMS = ["always", "never", "guaranteed"]
HEDGE_TERMS = ["at this stage", "based on", "one of the challenges", "may", "might", "likely", "uncertain"]
CASUAL_TERMS = ["hey", "awesome", "super", "kinda", "sorta", "lol"]

SENSITIVE_DISCLOSURE_CUES = [
    "not protectable",
    "cannot be protected",
    "managed retreat",
    "relocation is inevitable",
    "write off",
]


def _split_sentences(text: str) -> List[str]:
    parts = _SENT_SPLIT_RE.split((text or "").strip())
    return [p.strip() for p in parts if p and len(p.strip()) > 3]


def _normalize_world_chunks(retrieved_world_chunks: Sequence[Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for c in retrieved_world_chunks or []:
        if isinstance(c, str):
            txt = c.strip()
            rec = {
                "id": None,
                "text": txt,
                "metadata": {},
            }
        elif isinstance(c, dict):
            txt = str(c.get("text", "")).strip()
            rec = {
                "id": c.get("id") or (c.get("metadata") or {}).get("chunk_id"),
                "text": txt,
                "metadata": c.get("metadata", {}) or {},
            }
        else:
            txt = str(c).strip()
            rec = {
                "id": None,
                "text": txt,
                "metadata": {},
            }
        if txt:
            out.append(rec)
    return out


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _sentence_has_any(sentence: str, phrases: Sequence[str]) -> bool:
    low = sentence.lower()
    return any(p and p.lower() in low for p in phrases)


def _token_set(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", (text or "").lower()))


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a.intersection(b))
    union = len(a.union(b))
    return float(inter / max(1, union))


def _extract_parenthetical_acronyms(text: str) -> List[str]:
    return [m.upper() for m in re.findall(r"\(([A-Za-z]{2,10})\)", text or "")]


def _acronym_from_tokens(text: str) -> str:
    toks = re.findall(r"[A-Za-z]+", text or "")
    caps = [t[0].upper() for t in toks if t and t[0].isalpha()]
    if len(caps) >= 2:
        return "".join(caps)
    return ""


def classify_sentences(
    answer: str,
    persona_config: Dict[str, Any],
) -> List[Dict[str, Any]]:
    annotations: List[Dict[str, Any]] = []
    sentences = _split_sentences(answer)

    persona_name = str(persona_config.get("persona_name") or "").lower()
    role_title = str(persona_config.get("role_title") or "").lower()
    dept = str(persona_config.get("department_or_affiliation") or "").lower()

    for i, s in enumerate(sentences):
        s_low = s.lower()
        tags: List[str] = []

        has_world = any(k in s_low for k in WORLD_KEYWORDS)
        has_persona_fact = (
            any(c in s_low for c in PERSONA_FACT_CUES)
            or (persona_name and persona_name in s_low)
            or (role_title and role_title in s_low)
            or (dept and dept in s_low)
        )
        has_behavior = any(c in s_low for c in BEHAVIORAL_CUES) or any(h in s_low for h in HEDGE_TERMS)

        if has_world:
            tags.append("world_claim")
        if has_persona_fact:
            tags.append("persona_fact_claim")
        if has_behavior:
            tags.append("behavioral_expression")

        if len(tags) > 1:
            tags.append("mixed")

        if not tags:
            # default to world claim unless clearly generic small-talk
            tags = ["world_claim"]

        annotations.append({
            "sentence_index": i,
            "sentence": s,
            "labels": tags,
        })

    return annotations


def _encode_texts(embed_model: Any, texts: List[str]) -> np.ndarray:
    if not texts:
        return np.zeros((0, 768), dtype="float32")
    vecs = embed_model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    return np.asarray(vecs, dtype="float32")


def evaluate_world_faithfulness(
    annotations: List[Dict[str, Any]],
    world_chunks: List[Dict[str, Any]],
    embed_model: Any,
    support_threshold: float = 0.62,
    weak_support_margin: float = 0.08,
    contradiction_threshold: float = 0.30,
) -> Dict[str, Any]:
    world_sentences = [a["sentence"] for a in annotations if "world_claim" in a["labels"]]
    if not world_sentences:
        return {
            "supported_sentences": [],
            "weakly_supported_sentences": [],
            "unsupported_sentences": [],
            "contradictions": [],
            "grounding_score": 1.0,
        }

    if not world_chunks:
        return {
            "supported_sentences": [],
            "weakly_supported_sentences": [],
            "unsupported_sentences": [{"sentence": s, "best_world_chunk": "", "best_world_chunk_id": None, "similarity": 0.0} for s in world_sentences],
            "contradictions": [],
            "grounding_score": 0.0,
        }

    world_texts = [str(c.get("text", "")) for c in world_chunks]

    s_vecs = _encode_texts(embed_model, world_sentences)
    w_vecs = _encode_texts(embed_model, world_texts)

    sims = s_vecs @ w_vecs.T
    best_idx = sims.argmax(axis=1)
    best_sim = sims.max(axis=1)

    supported: List[Dict[str, Any]] = []
    weakly_supported: List[Dict[str, Any]] = []
    unsupported: List[Dict[str, Any]] = []
    contradictions: List[Dict[str, Any]] = []

    for i, sent in enumerate(world_sentences):
        sim = float(best_sim[i])
        idx = int(best_idx[i])
        chunk_rec = world_chunks[idx] if len(world_chunks) > 0 else {}
        chunk = str(chunk_rec.get("text", ""))
        chunk_id = chunk_rec.get("id") or (chunk_rec.get("metadata") or {}).get("chunk_id")
        sent_low = sent.lower()
        chunk_low = chunk.lower()

        neg_sent = bool(re.search(r"\b(no|not|never|none|cannot|can't)\b", sent_low))
        neg_chunk = bool(re.search(r"\b(no|not|never|none|cannot|can't)\b", chunk_low))

        if sim >= support_threshold:
            # contradiction heuristic: high overlap but opposite polarity
            if neg_sent != neg_chunk and sim >= max(0.62, support_threshold):
                contradictions.append({
                    "sentence": sent,
                    "best_world_chunk": chunk,
                    "best_world_chunk_id": chunk_id,
                    "similarity": round(sim, 4),
                    "reason": "possible polarity mismatch",
                })
            else:
                supported.append({
                    "sentence": sent,
                    "best_world_chunk": chunk,
                    "best_world_chunk_id": chunk_id,
                    "similarity": round(sim, 4),
                })
        elif sim >= max(0.0, support_threshold - weak_support_margin):
            weakly_supported.append({
                "sentence": sent,
                "best_world_chunk": chunk,
                "best_world_chunk_id": chunk_id,
                "similarity": round(sim, 4),
                "reason": "near-threshold semantic support",
            })
        else:
            # low-sim claims are unsupported, and very low + polar words treated as contradiction risk
            if sim <= contradiction_threshold and ("must" in sent_low or "never" in sent_low or "always" in sent_low):
                contradictions.append({
                    "sentence": sent,
                    "best_world_chunk": chunk,
                    "best_world_chunk_id": chunk_id,
                    "similarity": round(sim, 4),
                    "reason": "assertive claim with weak support",
                })
            else:
                unsupported.append({
                    "sentence": sent,
                    "best_world_chunk": chunk,
                    "best_world_chunk_id": chunk_id,
                    "similarity": round(sim, 4),
                })

    total = max(1, len(world_sentences))
    grounding_score = (len(supported) + 0.5 * len(weakly_supported)) / total
    grounding_score -= 0.5 * (len(contradictions) / total)
    grounding_score = max(0.0, min(1.0, grounding_score))

    return {
        "supported_sentences": supported,
        "weakly_supported_sentences": weakly_supported,
        "unsupported_sentences": unsupported,
        "contradictions": contradictions,
        "grounding_score": round(float(grounding_score), 4),
    }


def _build_persona_fact_map(persona_config: Dict[str, Any]) -> Dict[str, str]:
    wanted = [
        "persona_name",
        "role_title",
        "department_or_affiliation",
        "years_in_role",
        "total_experience",
        "residence",
        "education",
        "family_status",
    ]
    fact_map: Dict[str, str] = {}
    for k in wanted:
        v = persona_config.get(k)
        if v is None:
            continue
        if isinstance(v, (str, int, float)):
            sv = str(v).strip()
            if sv:
                fact_map[k] = sv
    return fact_map


def evaluate_persona_fact_faithfulness(
    annotations: List[Dict[str, Any]],
    persona_config: Dict[str, Any],
) -> Dict[str, Any]:
    fact_map = _build_persona_fact_map(persona_config)
    fact_sentences = [a["sentence"] for a in annotations if "persona_fact_claim" in a["labels"]]

    if not fact_sentences:
        return {
            "correct": [],
            "incorrect": [],
            "hallucinated": [],
            "score": 1.0,
        }

    correct: List[Dict[str, Any]] = []
    incorrect: List[Dict[str, Any]] = []
    hallucinated: List[Dict[str, Any]] = []

    expected_name = _slug(fact_map.get("persona_name", ""))
    expected_role = _slug(fact_map.get("role_title", ""))
    expected_dept = _slug(fact_map.get("department_or_affiliation", ""))
    expected_dept_tokens = _token_set(fact_map.get("department_or_affiliation", ""))
    expected_dept_acronyms = _extract_parenthetical_acronyms(fact_map.get("department_or_affiliation", ""))
    dept_generated_acronym = _acronym_from_tokens(fact_map.get("department_or_affiliation", ""))
    if dept_generated_acronym:
        expected_dept_acronyms.append(dept_generated_acronym)
    expected_dept_acronyms = sorted(set([a for a in expected_dept_acronyms if a]))

    for s in fact_sentences:
        s_low = s.lower()
        s_slug = _slug(s)
        s_tokens = _token_set(s)
        matched_fields: List[str] = []

        for k, v in fact_map.items():
            v_slug = _slug(v)
            if v_slug and v_slug in s_slug:
                matched_fields.append(k)
                continue

            # fuzzy token overlap for partial semantic variations in factual fields
            j = _jaccard(_token_set(v), s_tokens)
            if j >= 0.58:
                matched_fields.append(k)

        # Department acronym/partial-title support (e.g., DPCD or Department of Planning & Community Development)
        if "department_or_affiliation" in fact_map and "department_or_affiliation" not in matched_fields:
            dept_partial = "department of planning" in s_low or "planning & community development" in s_low or "planning and community development" in s_low
            dept_acr_hit = any(a.lower() in s_low for a in expected_dept_acronyms)
            dept_token_overlap = _jaccard(expected_dept_tokens, s_tokens) >= 0.42
            if dept_partial or dept_acr_hit or dept_token_overlap:
                matched_fields.append("department_or_affiliation")

        if matched_fields:
            correct.append({
                "sentence": s,
                "matched_fields": matched_fields,
                "matched_values": {k: fact_map.get(k) for k in matched_fields},
                "source": "persona_config",
            })
            continue

        # explicit contradiction checks
        name_m = re.search(r"\b(i am|my name is)\s+([a-z][a-z\s\-']+)\b", s_low)
        if name_m and expected_name:
            said = _slug(name_m.group(2))
            if said and said != expected_name:
                incorrect.append({
                    "sentence": s,
                    "reason": "name mismatch",
                    "expected": fact_map.get("persona_name"),
                    "source": "persona_config.persona_name",
                })
                continue

        role_m = re.search(r"\b(my role is|i am a|i work as)\s+([a-z][a-z\s\-']+)\b", s_low)
        if role_m and expected_role:
            said = _slug(role_m.group(2))
            if said and expected_role not in said:
                incorrect.append({
                    "sentence": s,
                    "reason": "role mismatch",
                    "expected": fact_map.get("role_title"),
                    "source": "persona_config.role_title",
                })
                continue

        dept_m = re.search(r"\b(my department is|i work at|i work in)\s+([a-z][a-z\s\-&()']+)\b", s_low)
        if dept_m and expected_dept:
            said = _slug(dept_m.group(2))
            if said and expected_dept not in said:
                incorrect.append({
                    "sentence": s,
                    "reason": "department mismatch",
                    "expected": fact_map.get("department_or_affiliation"),
                    "source": "persona_config.department_or_affiliation",
                })
                continue

        # numerical field mismatch if configured
        if "years_in_role" in fact_map:
            years = re.search(r"(\d+)\s+years", s_low)
            if years:
                expected_num = re.search(r"(\d+)", str(fact_map["years_in_role"]))
                if expected_num and years.group(1) != expected_num.group(1):
                    incorrect.append({
                        "sentence": s,
                        "reason": "years_in_role mismatch",
                        "expected": fact_map["years_in_role"],
                        "source": "persona_config.years_in_role",
                    })
                    continue

        # if it sounds like a persona fact but cannot be verified -> hallucinated
        if _sentence_has_any(s_low, list(PERSONA_FACT_CUES)):
            hallucinated.append({
                "sentence": s,
                "reason": "persona fact claim not found in persona_config",
                "source": "persona_config",
            })
        else:
            hallucinated.append({
                "sentence": s,
                "reason": "unverifiable persona fact",
                "source": "persona_config",
            })

    total = max(1, len(fact_sentences))
    score = (len(correct) + 0.4 * len(hallucinated)) / total - 0.6 * (len(incorrect) / total)
    score = max(0.0, min(1.0, score))

    return {
        "correct": correct,
        "incorrect": incorrect,
        "hallucinated": hallucinated,
        "score": round(float(score), 4),
    }


def evaluate_persona_behavior_fidelity(
    answer: str,
    persona_config: Dict[str, Any],
    question: Optional[str] = None,
    conversation_history: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    clean = (answer or "").strip()
    lower = clean.lower()

    # Tone adherence
    casual_hits = [w for w in CASUAL_TERMS if w in lower]
    abs_hits = [w for w in ABSOLUTE_TERMS if re.search(rf"\b{re.escape(w)}\b", lower)]
    hedge_hits = [w for w in HEDGE_TERMS if w in lower]

    tone_score = 1.0
    if casual_hits:
        tone_score -= min(0.35, 0.08 * len(casual_hits))
    if abs_hits:
        tone_score -= min(0.35, 0.12 * len(abs_hits))
    if hedge_hits:
        tone_score += min(0.2, 0.05 * len(hedge_hits))
    tone_score = max(0.0, min(1.0, tone_score))

    # Vocabulary usage
    vocab_pref = [v for v in (persona_config.get("vocabulary_preferences") or []) if isinstance(v, str)]
    expected_phrases = [
        "trade-offs",
        "feasibility",
        "at this stage",
    ]
    # include compact entries from config that look like phrases
    for p in vocab_pref:
        ps = p.strip()
        if 2 <= len(ps.split()) <= 5 and len(ps) <= 40:
            expected_phrases.append(ps)

    expected_phrases = list(dict.fromkeys([e.lower() for e in expected_phrases if e.strip()]))
    matched_expected = [e for e in expected_phrases if e in lower]
    vocab_match = (len(matched_expected) / max(1, min(len(expected_phrases), 12)))
    vocab_match = max(0.0, min(1.0, vocab_match))

    avoid_terms = [a.lower() for a in (persona_config.get("avoid_terms") or []) if isinstance(a, str)]
    avoid_hits = [a for a in avoid_terms if a and a in lower]

    guardrail_violations: List[str] = []

    # Guardrail checks
    if re.search(r"\b(neighborhood|district|area)s?\b.*\b(unviable|not viable|cannot be protected|not protectable)\b", lower):
        guardrail_violations.append("Named/implicit neighborhood unviability assertion")

    if re.search(r"\b(retreat|relocat(?:e|ion))\b.*\b(\d{4}|\d+\s+years?)\b", lower):
        guardrail_violations.append("Explicit retreat timeline")

    if re.search(r"\b(only solution|must do this|definitely|without question)\b", lower):
        guardrail_violations.append("Over-assertive conclusion")

    if re.search(r"\b(i recommend|you should|the town should immediately)\b", lower):
        guardrail_violations.append("Policy advocacy or directive stance")

    assistant_leakage = any(p in lower for p in ASSISTANT_LEAKAGE_PHRASES)
    if assistant_leakage:
        guardrail_violations.append("Generic assistant leakage phrasing")

    # Over-disclosure heuristic (tier-3 too early)
    q_low = (question or "").lower()
    turn_count = len(conversation_history or [])
    deep_prompt = bool(re.search(r"\b(why|ethical|trade-off|hypothetical|long-term|ambiguity)\b", q_low))
    sensitive_hits = [c for c in SENSITIVE_DISCLOSURE_CUES if c in lower]
    over_disclosure = bool(sensitive_hits and turn_count < 2 and not deep_prompt)

    behavior_score = 0.45 * tone_score + 0.30 * vocab_match
    if avoid_hits:
        behavior_score -= min(0.20, 0.08 * len(avoid_hits))
    if guardrail_violations:
        behavior_score -= min(0.35, 0.08 * len(guardrail_violations))
    if over_disclosure:
        behavior_score -= 0.18
    if assistant_leakage:
        behavior_score -= 0.15
    behavior_score = max(0.0, min(1.0, behavior_score))

    return {
        "tone_match": round(float(tone_score), 4),
        "vocab_match": round(float(vocab_match), 4),
        "guardrail_violations": guardrail_violations,
        "over_disclosure": over_disclosure,
        "assistant_leakage": assistant_leakage,
        "matched_expected_phrases": matched_expected[:8],
        "matched_avoid_terms": avoid_hits[:8],
        "score": round(float(behavior_score), 4),
    }


def infer_disclosure_mode(
    answer: str,
    question: Optional[str] = None,
    conversation_history: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    lower = (answer or "").lower()
    q_low = (question or "").lower()
    turns = len(conversation_history or [])

    if re.search(r"\b(it wouldn't be appropriate|cannot comment|can't speculate)\b", lower):
        predicted = "defensive"
    elif re.search(r"\b(hypothetically|if we imagine|if this were to happen)\b", lower):
        predicted = "hypothetical"
    elif re.search(r"\b(ethical|moral|tension|ambiguity|discomfort)\b", lower):
        predicted = "reflective"
    elif re.search(r"\b(one of the challenges|capacity|trade-offs|constraints)\b", lower) and turns >= 1:
        predicted = "elevated"
    else:
        predicted = "baseline"

    accusatory_q = bool(re.search(r"\b(why didn't you|isn't it true|you failed|you should have)\b", q_low))
    deep_q = bool(re.search(r"\b(why|ethical|trade-off|hypothetical|long-term|implications)\b", q_low))

    if predicted in {"baseline", "elevated"}:
        appropriate = True
    elif predicted == "defensive":
        appropriate = accusatory_q or turns >= 1
    elif predicted == "hypothetical":
        appropriate = "hypothetical" in q_low or "what if" in q_low
    else:  # reflective
        appropriate = deep_q or turns >= 2

    return {
        "predicted_mode": predicted,
        "appropriate": bool(appropriate),
    }


def aggregate_scores(
    world_score: float,
    persona_fact_score: float,
    behavior_score: float,
    disclosure_appropriate: bool,
) -> Tuple[float, str]:
    disclosure_score = 1.0 if disclosure_appropriate else 0.5
    overall = 0.45 * world_score + 0.25 * persona_fact_score + 0.25 * behavior_score + 0.05 * disclosure_score
    overall = max(0.0, min(1.0, overall))

    verdict = "GOOD" if overall >= 0.72 else "PARTIAL" if overall >= 0.45 else "FAIL"
    return round(float(overall), 4), verdict


def evaluate_multi_source_answer(
    answer: str,
    retrieved_world_chunks: Sequence[Any],
    persona_config: Dict[str, Any],
    conversation_history: Optional[Sequence[str]] = None,
    question: Optional[str] = None,
    embed_model: Optional[Any] = None,
    support_threshold: float = 0.62,
) -> Dict[str, Any]:
    if embed_model is None:
        raise ValueError("embed_model is required for world faithfulness evaluation")

    world_chunks = _normalize_world_chunks(retrieved_world_chunks)
    annotations = classify_sentences(answer=answer, persona_config=persona_config)

    world_eval = evaluate_world_faithfulness(
        annotations=annotations,
        world_chunks=world_chunks,
        embed_model=embed_model,
        support_threshold=support_threshold,
    )

    persona_fact_eval = evaluate_persona_fact_faithfulness(
        annotations=annotations,
        persona_config=persona_config,
    )

    behavior_eval = evaluate_persona_behavior_fidelity(
        answer=answer,
        persona_config=persona_config,
        question=question,
        conversation_history=conversation_history,
    )

    disclosure_eval = infer_disclosure_mode(
        answer=answer,
        question=question,
        conversation_history=conversation_history,
    )

    overall_score, verdict = aggregate_scores(
        world_score=float(world_eval["grounding_score"]),
        persona_fact_score=float(persona_fact_eval["score"]),
        behavior_score=float(behavior_eval["score"]),
        disclosure_appropriate=bool(disclosure_eval["appropriate"]),
    )

    return {
        "sentence_annotations": annotations,
        "world_faithfulness": world_eval,
        "persona_fact_faithfulness": persona_fact_eval,
        "persona_behavior_fidelity": behavior_eval,
        "disclosure_mode": disclosure_eval,
        "overall_score": overall_score,
        "verdict": verdict,
    }
