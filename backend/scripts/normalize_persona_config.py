"""
normalize_persona_config.py

Normalize a raw persona config JSON into a prompt-ready config.

Rules:
- clean bullets
- normalize disclosure mode schema
- normalize vocabulary/avoid lists
- keep missing fields null/empty (no invented facts)
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence


def _normalize_key(text: str) -> str:
    t = (text or "").strip().lower()
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"[^a-z0-9\s]", "", t)
    return t


def _normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _clean_item(x: str) -> Optional[str]:
    s = _normalize_ws(x)
    if not s or len(s) < 8:
        return None
    s = re.sub(r"\.{2,}", ".", s)
    s = re.sub(r"\s+([.,;:!?])", r"\1", s)
    s = s.strip("\"'“”‘’ ")
    if not s:
        return None
    if re.fullmatch(r"[\W_]+", s):
        return None
    return s


def _split_sentences(text: str) -> List[str]:
    if not text:
        return []
    t = _normalize_ws(text.replace(";", ". "))
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", t) if s.strip()]


def _split_dense(items: Sequence[str]) -> List[str]:
    out: List[str] = []
    for item in items:
        if not item:
            continue
        lines = [ln.strip() for ln in str(item).splitlines() if ln.strip()]
        parts = lines if len(lines) > 1 else _split_sentences(str(item))
        for p in parts:
            chunks = re.split(r"\s+[•\-]\s+|\s{2,}|\s+(?=(Typical\sLanguage|Pedagogical\sFunction|Core\sContent|Student\s))", p)
            for c in chunks:
                c = _normalize_ws(c)
                if c:
                    out.append(c)
    return out


def _dedupe(items: Sequence[str], max_items: Optional[int] = None) -> List[str]:
    out: List[str] = []
    seen = set()
    for it in items:
        c = _clean_item(it)
        if not c:
            continue
        k = _normalize_key(c)
        if not k or k in seen:
            continue
        out.append(c)
        seen.add(k)
        if max_items is not None and len(out) >= max_items:
            break
    return out


def _normalize_bullets(value: Any, max_items: int = 12) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        source = [str(x) for x in value]
    else:
        source = [str(value)]
    return _dedupe(_split_dense(source), max_items=max_items)


def _ensure_mode_schema(mode_data: Any) -> Dict[str, List[str]]:
    base = {"triggers": [], "response_style": [], "typical_language": [], "accessible_insight": []}

    if isinstance(mode_data, dict):
        for k in base:
            base[k] = _normalize_bullets(mode_data.get(k), max_items=8)
        return base

    # Backward compatibility if raw mode is list/string.
    items = _normalize_bullets(mode_data, max_items=20)
    base["triggers"] = [x for x in items if any(tok in _normalize_key(x) for tok in ("student", "when", "if", "asks", "question"))][:8]
    base["response_style"] = [x for x in items if any(tok in _normalize_key(x) for tok in ("response", "tone", "language", "procedural", "reflective"))][:8]
    base["typical_language"] = [x for x in items if any(tok in _normalize_key(x) for tok in ("typical language", "we need", "at this stage", "in a hypothetical"))][:6]
    base["accessible_insight"] = [x for x in items if any(tok in _normalize_key(x) for tok in ("pedagogical", "teaches", "insight", "learn", "function"))][:6]
    return base


def _concise_identity_summary(raw: Dict[str, Any]) -> Optional[str]:
    # Use provided short summary if present.
    existing = raw.get("short_identity_summary")
    if isinstance(existing, str) and _clean_item(existing):
        return _clean_item(existing)

    # Compose from existing factual fields only (no new facts).
    name = raw.get("persona_name")
    role = raw.get("role_title")
    dept = raw.get("department_or_affiliation")

    if isinstance(name, str) and isinstance(role, str) and name.strip() and role.strip():
        if isinstance(dept, str) and dept.strip():
            return f"{name.strip()} is {role.strip()} in {dept.strip()}."
        return f"{name.strip()} is {role.strip()}."

    return None


def normalize_persona_config_data(raw: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "persona_name": raw.get("persona_name"),
        "role_title": raw.get("role_title"),
        "department_or_affiliation": raw.get("department_or_affiliation"),
        "archetype": raw.get("archetype"),
        "short_identity_summary": _concise_identity_summary(raw),
        "lived_relationship_to_place": raw.get("lived_relationship_to_place"),
        "tone_rules": _normalize_bullets(raw.get("tone_rules"), max_items=10),
        "speech_patterns": _normalize_bullets(raw.get("speech_patterns"), max_items=10),
        "vocabulary_preferences": _normalize_bullets(raw.get("vocabulary_preferences"), max_items=20),
        "avoid_terms": _normalize_bullets(raw.get("avoid_terms"), max_items=20),
        "default_response_mode": raw.get("default_response_mode"),
        "disclosure_modes": {
            "baseline": _ensure_mode_schema((raw.get("disclosure_modes") or {}).get("baseline")),
            "elevated": _ensure_mode_schema((raw.get("disclosure_modes") or {}).get("elevated")),
            "reflective": _ensure_mode_schema((raw.get("disclosure_modes") or {}).get("reflective")),
            "defensive": _ensure_mode_schema((raw.get("disclosure_modes") or {}).get("defensive")),
            "hypothetical": _ensure_mode_schema((raw.get("disclosure_modes") or {}).get("hypothetical")),
        },
        "behavioral_rules": {
            "what_increases_openness": _normalize_bullets((raw.get("behavioral_rules") or {}).get("what_increases_openness"), max_items=8),
            "retreat_triggers_procedural_language": _normalize_bullets((raw.get("behavioral_rules") or {}).get("retreat_triggers_procedural_language"), max_items=8),
            "will_not_explicitly_say": _normalize_bullets((raw.get("behavioral_rules") or {}).get("will_not_explicitly_say"), max_items=8),
            "react_to_confrontational_questions": _normalize_bullets((raw.get("behavioral_rules") or {}).get("react_to_confrontational_questions"), max_items=8),
            "react_to_respectful_context_aware_questions": _normalize_bullets((raw.get("behavioral_rules") or {}).get("react_to_respectful_context_aware_questions"), max_items=8),
        },
        "guardrails": _normalize_bullets(raw.get("guardrails"), max_items=20),
        "ethical_tensions": _normalize_bullets(raw.get("ethical_tensions"), max_items=20),
        "core_tensions": _normalize_bullets(raw.get("core_tensions"), max_items=20),
        "knowledge_tiers": {
            "tier_1_public": _normalize_bullets((raw.get("knowledge_tiers") or {}).get("tier_1_public"), max_items=20),
            "tier_2_contextual": _normalize_bullets((raw.get("knowledge_tiers") or {}).get("tier_2_contextual"), max_items=20),
            "tier_3_sensitive": _normalize_bullets((raw.get("knowledge_tiers") or {}).get("tier_3_sensitive"), max_items=20),
        },
        "relationship_map": _normalize_bullets(raw.get("relationship_map"), max_items=20),
        "assessment_hooks": _normalize_bullets(raw.get("assessment_hooks"), max_items=20),
        "simulation_function": raw.get("simulation_function"),
        "metadata": {
            "normalized_from": "raw_persona_config",
            "normalizer": "normalize_persona_config.py",
            "source_metadata": raw.get("metadata", {}),
        },
    }

    # keep null if not confidently extracted upstream
    if not out["short_identity_summary"]:
        out["short_identity_summary"] = None

    return out


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(payload: Dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Normalize raw persona config into prompt-ready JSON")
    parser.add_argument("--input-json", type=Path, required=True, help="Raw persona config JSON")
    parser.add_argument("--output-json", type=Path, required=True, help="Normalized output JSON")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    raw = load_json(args.input_json)
    norm = normalize_persona_config_data(raw)
    save_json(norm, args.output_json)

    print("Normalized persona config created:")
    print(f"- Input: {args.input_json}")
    print(f"- Output: {args.output_json}")


if __name__ == "__main__":
    main()
