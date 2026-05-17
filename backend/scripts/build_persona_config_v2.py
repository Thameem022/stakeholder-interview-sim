"""
build_persona_config_v2.py

Reusable, section-aware persona dossier extractor.

Outputs:
- raw extracted config (with field-level debug metadata)
- normalized prompt-ready config (optional, via normalize_persona_config.py)
"""

from __future__ import annotations

import argparse
import importlib
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


@dataclass
class SectionBlock:
    section_title: Optional[str]
    subsection_title: Optional[str]
    subsubsection_title: Optional[str]
    content: str


@dataclass
class HeadingEntry:
    raw_heading: str
    normalized_heading: str
    content: str


SECTION_ALIAS_GROUPS: Dict[str, List[Tuple[str, ...]]] = {
    "identity": [
        ("role", "institutional", "position"),
        ("identity",),
        ("persona", "profile"),
        ("stakeholder", "profile"),
        ("role", "title"),
    ],
    "relationship_to_place": [
        ("relationship", "harbortown"),
        ("relationship", "place"),
        ("lived", "relationship"),
    ],
    "archetype": [
        ("interview", "personality", "archetype"),
        ("personality", "archetype"),
        ("archetype",),
    ],
    "conversational_style": [
        ("conversational", "style"),
        ("communication", "style"),
        ("tone",),
        ("speech",),
        ("vocabulary",),
        ("linguistic",),
    ],
    "knowledge_inventory": [
        ("knowledge", "inventory"),
        ("tier", "1", "knowledge"),
        ("tier", "2", "knowledge"),
        ("tier", "3", "knowledge"),
        ("knowledge",),
    ],
    "disclosure_logic": [
        ("disclosure", "logic"),
        ("disclosure", "modes"),
        ("response", "dynamics"),
        ("disclosure",),
    ],
    "design_guardrails": [
        ("design", "guardrails"),
        ("guardrails",),
    ],
    "ethical_tensions": [
        ("ethical", "tensions"),
        ("moral", "stress"),
    ],
    "core_tensions": [
        ("core", "tensions"),
        ("defining", "tension"),
    ],
    "assessment": [
        ("assessment", "hooks"),
        ("assessment",),
        ("iqr",),
        ("sic",),
    ],
    "simulation_function": [
        ("simulation", "function"),
    ],
    "relationship_map": [
        ("relationship", "map"),
        ("stakeholder", "map"),
        ("institutional", "relationships"),
    ],
}


def _normalize_key(text: str) -> str:
    t = (text or "").strip().lower()
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"[^a-z0-9\s]", "", t)
    return t


def _normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _heading_level(paragraph_style_name: str) -> Optional[int]:
    if not paragraph_style_name:
        return None
    m = re.match(r"^Heading\s+(\d+)$", paragraph_style_name.strip(), flags=re.IGNORECASE)
    return int(m.group(1)) if m else None


def _infer_heading_level_from_text(text: str) -> Optional[int]:
    t = (text or "").strip()
    if not t:
        return None
    if re.match(r"^section\s+\d+\b", t, flags=re.IGNORECASE):
        return 1
    if re.match(r"^\d+\.\d+\b", t):
        return 2
    if re.match(r"^\d+\.\d+\.\d+\b", t):
        return 3
    if len(t) <= 90 and t.endswith(":"):
        return 2
    return None


def _is_age_like(value: str) -> bool:
    v = _normalize_key(value)
    return bool(re.match(r"^\d{1,3}$", v) or re.match(r"^age\s*\d{1,3}$", v))


def _extract_first_line(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    for ln in text.splitlines():
        x = ln.strip(" -•\t")
        if x:
            return x
    return None


def _split_sentences(text: str) -> List[str]:
    if not text:
        return []
    t = " ".join(ln.strip() for ln in text.splitlines() if ln.strip())
    t = t.replace(";", ". ")
    t = re.sub(r"\s+", " ", t).strip()
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", t) if s.strip()]


def _clean_fragment(x: str) -> Optional[str]:
    s = _normalize_ws(x)
    if not s or len(s) < 8:
        return None
    if re.fullmatch(r"[\W_]+", s):
        return None
    s = re.sub(r"\.{2,}", ".", s)
    s = re.sub(r"\s+([.,;:!?])", r"\1", s).strip("\"'“”‘’ ")
    if s in {"never", "wrong", "fair", "guaranteed"}:
        return None
    return s or None


def _split_dense_text(text: str) -> List[str]:
    if not text:
        return []
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    candidates = lines if len(lines) > 1 else _split_sentences(text)
    expanded: List[str] = []
    for c in candidates:
        parts = re.split(r"\s+[•\-]\s+|\s{2,}|\s+(?=(Student\s|Typical\sLanguage|Pedagogical\sFunction|Core\sContent))", c)
        for p in parts:
            p = _normalize_ws(p)
            if p:
                expanded.append(p)
    return expanded


def _dedupe_list(items: Sequence[str], max_items: Optional[int] = None) -> List[str]:
    out: List[str] = []
    seen = set()
    for item in items:
        cleaned = _clean_fragment(item)
        if not cleaned:
            continue
        k = _normalize_key(cleaned)
        if not k or k in seen:
            continue
        out.append(cleaned)
        seen.add(k)
        if max_items is not None and len(out) >= max_items:
            break
    return out


def _to_bullets(text: Optional[str], max_items: int = 12) -> List[str]:
    if not text:
        return []
    return _dedupe_list(_split_dense_text(text), max_items=max_items)


def _select_bullets_by_keywords(items: Sequence[str], keywords: Sequence[str], max_items: int = 6) -> List[str]:
    sel = []
    for it in items:
        n = _normalize_key(it)
        if any(k in n for k in keywords):
            sel.append(it)
    return _dedupe_list(sel, max_items=max_items)


def _extract_labeled_sections(text: str, label_aliases: Dict[str, List[str]]) -> Dict[str, str]:
    result: Dict[str, List[str]] = {k: [] for k in label_aliases}
    if not text:
        return {k: "" for k in label_aliases}

    alias_to_key: Dict[str, str] = {}
    for key, aliases in label_aliases.items():
        for a in aliases:
            alias_to_key[_normalize_key(a)] = key

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    current: Optional[str] = None
    for ln in lines:
        nln = _normalize_key(ln)
        matched: Optional[str] = None
        for alias, key in alias_to_key.items():
            if nln.startswith(alias):
                matched = key
                break
        if matched:
            current = matched
            after = re.sub(r"^[^:]{1,90}:\s*", "", ln).strip()
            if after and after != ln:
                result[current].append(after)
            continue
        if current:
            result[current].append(ln)

    return {k: "\n".join(v).strip() for k, v in result.items()}


def load_docx_with_structure(input_docx: Path) -> List[Dict[str, Any]]:
    if not input_docx.exists():
        raise FileNotFoundError(f"Input .docx not found: {input_docx}")

    try:
        docx_module = importlib.import_module("docx")
        Document = getattr(docx_module, "Document")
    except Exception as exc:
        raise ImportError("python-docx is required. Install with: pip install python-docx") from exc

    doc = Document(str(input_docx))
    rows: List[Dict[str, Any]] = []
    for p in doc.paragraphs:
        text = (p.text or "").strip()
        if not text:
            continue
        style_name = (p.style.name if p.style is not None else "") or ""
        lvl = _heading_level(style_name)
        if lvl is None:
            lvl = _infer_heading_level_from_text(text)
        rows.append({"text": text, "is_heading": lvl is not None, "heading_level": lvl, "style_name": style_name})
    return rows


def build_section_blocks(rows: Sequence[Dict[str, Any]]) -> List[SectionBlock]:
    blocks: List[SectionBlock] = []
    h1: Optional[str] = None
    h2: Optional[str] = None
    h3: Optional[str] = None
    buffer: List[str] = []

    def flush() -> None:
        nonlocal buffer
        if not buffer:
            return
        t = "\n\n".join(buffer).strip()
        if t:
            blocks.append(SectionBlock(h1, h2, h3, t))
        buffer = []

    for row in rows:
        if row["is_heading"]:
            flush()
            level = int(row.get("heading_level") or 0)
            ht = row["text"].strip()
            if level == 1:
                h1, h2, h3 = ht, None, None
            elif level == 2:
                h2, h3 = ht, None
            else:
                h3 = ht
            continue
        buffer.append(row["text"])

    flush()
    return blocks


def build_heading_entries(blocks: Sequence[SectionBlock]) -> List[HeadingEntry]:
    entries: List[HeadingEntry] = []
    for b in blocks:
        for h in (b.section_title, b.subsection_title, b.subsubsection_title):
            if not h:
                continue
            nh = _normalize_key(h)
            if nh:
                entries.append(HeadingEntry(h, nh, b.content))
    return entries


def _match_section_key(normalized_heading: str) -> Optional[str]:
    best_key = None
    best_score = 0
    for key, groups in SECTION_ALIAS_GROUPS.items():
        for g in groups:
            if all(tok in normalized_heading for tok in g):
                if len(g) > best_score:
                    best_key, best_score = key, len(g)
    return best_key


def build_section_index(entries: Sequence[HeadingEntry]) -> Dict[str, List[HeadingEntry]]:
    idx: Dict[str, List[HeadingEntry]] = {k: [] for k in SECTION_ALIAS_GROUPS}
    for e in entries:
        sec = _match_section_key(e.normalized_heading)
        if sec:
            idx[sec].append(e)
    return idx


def _merge_contents(entries: Sequence[HeadingEntry]) -> str:
    seen = set()
    out: List[str] = []
    for e in entries:
        k = _normalize_key(e.content)
        if k and k not in seen:
            out.append(e.content)
            seen.add(k)
    return "\n\n".join(out).strip()


def _extract_key_values(text: str) -> Dict[str, str]:
    kv: Dict[str, str] = {}
    rx = re.compile(r"^\s*([A-Za-z][A-Za-z0-9\s/&\-]{1,80})\s*:\s*(.+?)\s*$")
    for ln in text.splitlines():
        m = rx.match(ln)
        if not m:
            continue
        k = _normalize_key(m.group(1))
        v = _normalize_ws(m.group(2))
        if k and v and k not in kv:
            kv[k] = v
    return kv


def _set_debug(debug: Dict[str, Dict[str, Any]], field: str, source_heading: Optional[str], method: str, confidence: float) -> None:
    debug[field] = {"source_heading": source_heading, "extraction_method": method, "confidence": round(float(confidence), 3)}


def _warn_if_missing(field_name: str, value: Any) -> None:
    if value is None or value == [] or value == {}:
        logging.warning("Could not confidently extract '%s'; leaving null/empty.", field_name)


def _filter_entries(entries: Sequence[HeadingEntry], include: Sequence[Tuple[str, ...]], exclude: Optional[Sequence[Tuple[str, ...]]] = None) -> List[HeadingEntry]:
    out: List[HeadingEntry] = []
    for e in entries:
        n = e.normalized_heading
        if not any(all(tok in n for tok in g) for g in include):
            continue
        if exclude and any(all(tok in n for tok in ex) for ex in exclude):
            continue
        out.append(e)
    return out


def extract_identity(section_index: Dict[str, List[HeadingEntry]], persona_name_hint: Optional[str], debug: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    identity_text = _merge_contents(section_index.get("identity", []))
    rel_text = _merge_contents(section_index.get("relationship_to_place", []))
    kv = _extract_key_values(identity_text)

    persona_name = persona_name_hint
    if persona_name:
        _set_debug(debug, "persona_name", None, "cli_override", 1.0)
    if not persona_name:
        for key in ("name", "persona name", "stakeholder name"):
            v = kv.get(key)
            if v and not _is_age_like(v):
                persona_name = v
                _set_debug(debug, "persona_name", "identity", f"identity_kv:{key}", 0.98)
                break

    role_title = None
    for key in ("role title", "job title", "title", "position"):
        v = kv.get(key)
        if v and not _is_age_like(v):
            role_title = v
            _set_debug(debug, "role_title", "identity", f"identity_kv:{key}", 0.96)
            break

    department = None
    for key in ("department", "department or affiliation", "affiliation", "office", "agency", "unit"):
        v = kv.get(key)
        if v and not _is_age_like(v):
            department = v
            _set_debug(debug, "department_or_affiliation", "identity", f"identity_kv:{key}", 0.96)
            break

    short_identity_summary = None
    non_kv = []
    for ln in identity_text.splitlines():
        s = ln.strip()
        if not s:
            continue
        if re.match(r"^[A-Za-z][A-Za-z0-9\s/&\-]{1,80}:\s+.+$", s):
            continue
        if s.endswith(":"):
            continue
        non_kv.append(s)
    if non_kv:
        short_identity_summary = _clean_fragment(non_kv[0])
        if short_identity_summary:
            _set_debug(debug, "short_identity_summary", "identity", "identity_non_kv_first_line", 0.72)

    lived_relationship = _extract_first_line(rel_text)
    if lived_relationship:
        _set_debug(debug, "lived_relationship_to_place", "relationship_to_place", "relationship_first_line", 0.86)

    out = {
        "persona_name": persona_name,
        "role_title": role_title,
        "department_or_affiliation": department,
        "short_identity_summary": short_identity_summary,
        "lived_relationship_to_place": lived_relationship,
    }
    for k, v in out.items():
        _warn_if_missing(k, v)
    return out


def extract_archetype(section_index: Dict[str, List[HeadingEntry]], debug: Dict[str, Dict[str, Any]]) -> Optional[str]:
    text = _merge_contents(section_index.get("archetype", []))
    line = _extract_first_line(text) or ""
    if not line:
        _warn_if_missing("archetype", None)
        return None

    m = re.search(r"\bas\s+(?:a|an)\s+([A-Z][A-Za-z\- ]{2,80}?)(?:[—\-,]|\s+who\b|\.)", line)
    if m:
        val = _clean_fragment(m.group(1))
        if val:
            _set_debug(debug, "archetype", "archetype", "label_reduce_as_a_an", 0.9)
            return val

    m2 = re.search(r"(?i)archetype\s*:\s*(.+)$", line)
    if m2:
        val = _clean_fragment(re.split(r"[—\-,:]", m2.group(1))[0])
        if val:
            _set_debug(debug, "archetype", "archetype", "kv_like_archetype", 0.88)
            return val

    if len(line) <= 80:
        val2 = _clean_fragment(line)
        if val2:
            _set_debug(debug, "archetype", "archetype", "first_line_short", 0.7)
            return val2

    _warn_if_missing("archetype", None)
    return None


def extract_style(section_index: Dict[str, List[HeadingEntry]], debug: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    entries = section_index.get("conversational_style", [])
    tone_entries = _filter_entries(entries, [("tone",), ("communicative", "posture"), ("overall", "tone")])
    speech_entries = _filter_entries(entries, [("speech",), ("pacing",), ("rhythm",)], exclude=[("tone",)])
    vocab_entries = _filter_entries(entries, [("vocabulary",), ("linguistic",), ("preferred", "vocabulary")])
    avoid_entries = _filter_entries(entries, [("avoid",), ("avoids",), ("avoid", "terms")])

    tone_rules = _to_bullets(_merge_contents(tone_entries), 12)
    speech_patterns = _to_bullets(_merge_contents(speech_entries), 12)
    vocabulary_preferences = _to_bullets(_merge_contents(vocab_entries), 20)
    avoid_terms = _to_bullets(_merge_contents(avoid_entries), 20)

    used = set()
    tone_rules = [x for x in tone_rules if (_normalize_key(x) not in used and not used.add(_normalize_key(x)))]
    speech_patterns = [x for x in speech_patterns if (_normalize_key(x) not in used and not used.add(_normalize_key(x)))]

    default_response_mode = None
    kv = _extract_key_values(_merge_contents(entries))
    for key in ("default response mode", "response mode", "default stance", "baseline mode"):
        v = kv.get(key)
        if v and len(v) <= 120:
            default_response_mode = v
            _set_debug(debug, "default_response_mode", "conversational_style", f"style_kv:{key}", 0.95)
            break

    if tone_entries:
        _set_debug(debug, "tone_rules", tone_entries[0].raw_heading, "style_subsection_extract", 0.9)
    if speech_entries:
        _set_debug(debug, "speech_patterns", speech_entries[0].raw_heading, "style_subsection_extract", 0.9)
    if vocab_entries:
        _set_debug(debug, "vocabulary_preferences", vocab_entries[0].raw_heading, "style_subsection_extract", 0.88)
    if avoid_entries:
        _set_debug(debug, "avoid_terms", avoid_entries[0].raw_heading, "style_subsection_extract", 0.86)

    out = {
        "tone_rules": tone_rules,
        "speech_patterns": speech_patterns,
        "vocabulary_preferences": vocabulary_preferences,
        "avoid_terms": avoid_terms,
        "default_response_mode": default_response_mode,
    }
    for k, v in out.items():
        _warn_if_missing(k, v)
    return out


def extract_knowledge_tiers(section_index: Dict[str, List[HeadingEntry]], debug: Dict[str, Dict[str, Any]]) -> Dict[str, List[str]]:
    entries = section_index.get("knowledge_inventory", [])
    t1 = _filter_entries(entries, [("tier", "1"), ("public",)])
    t2 = _filter_entries(entries, [("tier", "2"), ("contextual",)])
    t3 = _filter_entries(entries, [("tier", "3"), ("sensitive",)])

    tiers = {
        "tier_1_public": _to_bullets(_merge_contents(t1), 25),
        "tier_2_contextual": _to_bullets(_merge_contents(t2), 25),
        "tier_3_sensitive": _to_bullets(_merge_contents(t3), 25),
    }

    if t1:
        _set_debug(debug, "knowledge_tiers.tier_1_public", t1[0].raw_heading, "tier_subsection_extract", 0.9)
    if t2:
        _set_debug(debug, "knowledge_tiers.tier_2_contextual", t2[0].raw_heading, "tier_subsection_extract", 0.9)
    if t3:
        _set_debug(debug, "knowledge_tiers.tier_3_sensitive", t3[0].raw_heading, "tier_subsection_extract", 0.9)

    _warn_if_missing("knowledge_tiers", tiers if any(tiers.values()) else {})
    return tiers


def _build_structured_mode(mode_text: str) -> Dict[str, List[str]]:
    bullets = _to_bullets(mode_text, 30)
    return {
        "triggers": _select_bullets_by_keywords(bullets, ["student", "when", "if", "asks", "question", "confront", "respectful", "pushes", "paraphrases"], 8),
        "response_style": _select_bullets_by_keywords(bullets, ["response", "tone", "language", "returns", "shortens", "lengthens", "cautious", "reflective", "procedural"], 8),
        "typical_language": _select_bullets_by_keywords(bullets, ["typical language", "we need", "it wouldnt be appropriate", "in a hypothetical", "at this stage"], 6),
        "accessible_insight": _select_bullets_by_keywords(bullets, ["pedagogical", "teaches", "insight", "learn", "function", "rewards", "unlocks"], 6),
    }


def extract_disclosure_modes(section_index: Dict[str, List[HeadingEntry]], debug: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, List[str]]]:
    entries = section_index.get("disclosure_logic", [])
    text = _merge_contents(entries)

    mode_aliases = {
        "baseline": ["baseline", "baseline mode"],
        "elevated": ["elevated", "elevated mode"],
        "reflective": ["reflective", "reflective mode"],
        "defensive": ["defensive", "defensive mode"],
        "hypothetical": ["hypothetical", "hypothetical mode"],
    }

    modes: Dict[str, Dict[str, List[str]]] = {m: {"triggers": [], "response_style": [], "typical_language": [], "accessible_insight": []} for m in mode_aliases}
    labeled = _extract_labeled_sections(text, mode_aliases)

    for mode in mode_aliases:
        m_entries = _filter_entries(entries, [("disclosure", mode), (mode, "mode"), (mode,)])
        mode_text = labeled.get(mode) or _merge_contents(m_entries)
        modes[mode] = _build_structured_mode(mode_text)

    if entries:
        _set_debug(debug, "disclosure_modes", entries[0].raw_heading, "disclosure_section_structured_extract", 0.82)

    _warn_if_missing("disclosure_modes", modes if any(any(v.values()) for v in modes.values()) else {})
    return modes


def extract_behavioral_rules(section_index: Dict[str, List[HeadingEntry]], disclosure_modes: Dict[str, Dict[str, List[str]]], debug: Dict[str, Dict[str, Any]]) -> Dict[str, List[str]]:
    entries = section_index.get("disclosure_logic", [])
    bullets = _to_bullets(_merge_contents(entries), 80)

    rules = {
        "what_increases_openness": _select_bullets_by_keywords(
            bullets + disclosure_modes.get("elevated", {}).get("triggers", []) + disclosure_modes.get("reflective", {}).get("triggers", []),
            ["respectful", "context", "trust", "listening", "paraphrase", "awareness", "institutional constraints"],
            8,
        ),
        "retreat_triggers_procedural_language": _select_bullets_by_keywords(
            bullets + disclosure_modes.get("defensive", {}).get("triggers", []),
            ["confront", "accusatory", "leading", "procedural", "shortens", "speculate", "commitments"],
            8,
        ),
        "will_not_explicitly_say": _select_bullets_by_keywords(
            bullets,
            ["will not", "never", "does not", "not cross", "boundaries", "wont"],
            8,
        ),
        "react_to_confrontational_questions": _select_bullets_by_keywords(
            bullets + disclosure_modes.get("defensive", {}).get("response_style", []),
            ["confront", "accusatory", "returns to", "procedural", "shortens responses"],
            8,
        ),
        "react_to_respectful_context_aware_questions": _select_bullets_by_keywords(
            bullets + disclosure_modes.get("elevated", {}).get("response_style", []) + disclosure_modes.get("reflective", {}).get("response_style", []),
            ["respectful", "context", "lengthen", "reflective", "qualifiers", "acknowledges constraints"],
            8,
        ),
    }

    if entries:
        _set_debug(debug, "behavioral_rules", entries[0].raw_heading, "disclosure_section_keyword_rules", 0.78)

    _warn_if_missing("behavioral_rules", rules if any(rules.values()) else {})
    return rules


def extract_governance(section_index: Dict[str, List[HeadingEntry]], debug: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    guard_entries = section_index.get("design_guardrails", [])
    ethical_entries = section_index.get("ethical_tensions", [])
    core_entries = section_index.get("core_tensions", [])
    assess_entries = section_index.get("assessment", [])
    sim_entries = section_index.get("simulation_function", [])
    rel_entries = section_index.get("relationship_map", [])

    guardrails = _to_bullets(_merge_contents(guard_entries), 20)
    ethical_tensions = _to_bullets(_merge_contents(ethical_entries), 20)
    core_tensions = _to_bullets(_merge_contents(core_entries), 20)

    used = set()
    guardrails = [x for x in guardrails if (_normalize_key(x) not in used and not used.add(_normalize_key(x)))]
    ethical_tensions = [x for x in ethical_tensions if (_normalize_key(x) not in used and not used.add(_normalize_key(x)))]
    core_tensions = [x for x in core_tensions if (_normalize_key(x) not in used and not used.add(_normalize_key(x)))]

    relationship_map = _to_bullets(_merge_contents(rel_entries), 20)
    assessment_hooks = _to_bullets(_merge_contents(assess_entries), 20)
    simulation_function = _extract_first_line(_merge_contents(sim_entries))
    if simulation_function and len(simulation_function) > 180:
        simulation_function = None

    if guard_entries:
        _set_debug(debug, "guardrails", guard_entries[0].raw_heading, "guardrail_section_extract", 0.9)
    if ethical_entries:
        _set_debug(debug, "ethical_tensions", ethical_entries[0].raw_heading, "ethical_section_extract", 0.9)
    if core_entries:
        _set_debug(debug, "core_tensions", core_entries[0].raw_heading, "core_tension_section_extract", 0.9)
    if rel_entries:
        _set_debug(debug, "relationship_map", rel_entries[0].raw_heading, "relationship_map_section_extract", 0.84)
    if assess_entries:
        _set_debug(debug, "assessment_hooks", assess_entries[0].raw_heading, "assessment_section_extract", 0.84)
    if sim_entries and simulation_function:
        _set_debug(debug, "simulation_function", sim_entries[0].raw_heading, "simulation_section_first_line", 0.9)

    out = {
        "guardrails": guardrails,
        "ethical_tensions": ethical_tensions,
        "core_tensions": core_tensions,
        "relationship_map": relationship_map,
        "assessment_hooks": assessment_hooks,
        "simulation_function": simulation_function,
    }

    for k, v in out.items():
        _warn_if_missing(k, v)
    return out


def build_persona_config(blocks: Sequence[SectionBlock], persona_name_hint: Optional[str] = None) -> Dict[str, Any]:
    heading_entries = build_heading_entries(blocks)
    section_index = build_section_index(heading_entries)
    extraction_debug: Dict[str, Dict[str, Any]] = {}

    identity = extract_identity(section_index, persona_name_hint, extraction_debug)
    archetype = extract_archetype(section_index, extraction_debug)
    style = extract_style(section_index, extraction_debug)
    tiers = extract_knowledge_tiers(section_index, extraction_debug)
    disclosure_modes = extract_disclosure_modes(section_index, extraction_debug)
    behavioral_rules = extract_behavioral_rules(section_index, disclosure_modes, extraction_debug)
    governance = extract_governance(section_index, extraction_debug)

    return {
        "persona_name": identity["persona_name"],
        "role_title": identity["role_title"],
        "department_or_affiliation": identity["department_or_affiliation"],
        "archetype": archetype,
        "short_identity_summary": identity["short_identity_summary"],
        "lived_relationship_to_place": identity["lived_relationship_to_place"],
        "tone_rules": style["tone_rules"],
        "speech_patterns": style["speech_patterns"],
        "vocabulary_preferences": style["vocabulary_preferences"],
        "avoid_terms": style["avoid_terms"],
        "default_response_mode": style["default_response_mode"],
        "disclosure_modes": disclosure_modes,
        "behavioral_rules": behavioral_rules,
        "guardrails": governance["guardrails"],
        "ethical_tensions": governance["ethical_tensions"],
        "core_tensions": governance["core_tensions"],
        "knowledge_tiers": tiers,
        "relationship_map": governance["relationship_map"],
        "assessment_hooks": governance["assessment_hooks"],
        "simulation_function": governance["simulation_function"],
        "metadata": {
            "source_type": "docx",
            "parser": "section_aware_heading_alias_rule_based",
            "block_count": len(blocks),
            "extraction_policy": "no_invention_missing_as_null_or_empty",
            "section_aliases": {k: [" ".join(g) for g in v] for k, v in SECTION_ALIAS_GROUPS.items()},
            "extraction_debug": extraction_debug,
        },
    }


def save_json(payload: Dict[str, Any], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build section-aware persona config from dossier .docx")
    parser.add_argument("--input-docx", type=Path, required=True, help="Path to persona dossier .docx")
    parser.add_argument("--output-json", type=Path, default=None, help="Raw output JSON path (default: <input_stem>.raw_persona_config.json)")
    parser.add_argument("--normalized-output", type=Path, default=None, help="Normalized output JSON path (default: <input_stem>.persona_config.json)")
    parser.add_argument("--skip-normalize", action="store_true", help="Only write raw output")
    parser.add_argument("--persona-name", type=str, default=None, help="Optional persona name override")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    return parser.parse_args()


def _derive_output_paths(input_docx: Path, raw_out: Optional[Path], norm_out: Optional[Path]) -> Tuple[Path, Path]:
    raw_path = raw_out or input_docx.with_name(f"{input_docx.stem}.raw_persona_config.json")
    norm_path = norm_out or input_docx.with_name(f"{input_docx.stem}.persona_config.json")
    return raw_path, norm_path


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s: %(message)s")

    raw_out, norm_out = _derive_output_paths(args.input_docx, args.output_json, args.normalized_output)

    try:
        rows = load_docx_with_structure(args.input_docx)
        blocks = build_section_blocks(rows)
        if not blocks:
            raise ValueError("No content blocks extracted from dossier.")

        raw_config = build_persona_config(blocks, persona_name_hint=args.persona_name)
        save_json(raw_config, raw_out)

        normalized_written = False
        if not args.skip_normalize:
            try:
                normalizer_module = importlib.import_module("backend.normalize_persona_config")
            except Exception:
                normalizer_module = importlib.import_module("normalize_persona_config")

            normalize_fn = getattr(normalizer_module, "normalize_persona_config_data")
            normalized = normalize_fn(raw_config)
            save_json(normalized, norm_out)
            normalized_written = True

    except Exception as exc:
        raise SystemExit(f"Failed to build persona config: {exc}") from exc

    print("\nPersona config extraction completed:")
    print(f"- Input: {args.input_docx}")
    print(f"- Raw output: {raw_out}")
    if not args.skip_normalize and normalized_written:
        print(f"- Normalized output: {norm_out}")

    print("\nExample run:")
    print(
        "python backend/build_persona_config.py --input-docx backend/Persona_dossier/Persona 1_Municipal Planner Dossier.docx "
        "--output-json backend/persona_configs/planner_persona_config.raw.json "
        "--normalized-output backend/persona_configs/planner_persona_config.json"
    )


if __name__ == "__main__":
    main()
