"""
build_persona_config.py

Convert a stakeholder persona dossier (.docx) into a structured persona configuration JSON
for an AI interview simulator.

The script is intentionally conservative:
- It uses heading-aware extraction and deterministic parsing.
- It does not invent missing details.
- If fields cannot be extracted confidently, they remain null/empty and warnings are logged.

Example:
    /opt/anaconda3/envs/glob/bin/python backend/build_persona_config.py \
      --input-docx "backend/Persona_dossier/Persona 1_Municipal Planner Dossier.docx" \
      --output-json "backend/persona_configs/planner_persona_config.json"
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


# Delegate runtime to refactored section-aware pipeline while keeping this entrypoint path stable.
if __name__ == "__main__":
    try:
        from backend.build_persona_config_v2 import main as _v2_main
    except Exception:
        from build_persona_config_v2 import main as _v2_main
    _v2_main()
    raise SystemExit(0)


@dataclass
class SectionBlock:
    """A heading-scoped text block from the dossier."""

    section_title: Optional[str]
    subsection_title: Optional[str]
    subsubsection_title: Optional[str]
    content: str


@dataclass
class HeadingEntry:
    """Heading index entry with associated content block."""

    raw_heading: str
    normalized_heading: str
    content: str


def _normalize_key(text: str) -> str:
    t = (text or "").strip().lower()
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"[^a-z0-9\s]", "", t)
    return t


def _heading_level(paragraph_style_name: str) -> Optional[int]:
    """Return heading level from style like 'Heading 1'."""
    if not paragraph_style_name:
        return None
    m = re.match(r"^Heading\s+(\d+)$", paragraph_style_name.strip(), flags=re.IGNORECASE)
    if not m:
        return None
    return int(m.group(1))


def _infer_heading_level_from_text(text: str) -> Optional[int]:
    """Fallback heading level detection if style metadata is inconsistent."""
    t = (text or "").strip()
    if not t:
        return None

    # Common dossier heading markers.
    if re.match(r"^section\s+\d+\b", t, flags=re.IGNORECASE):
        return 1
    if re.match(r"^\d+\.\d+\b", t):
        return 2
    if re.match(r"^\d+\.\d+\.\d+\b", t):
        return 3

    # If a line is short and title-like, treat as potential heading 2.
    if len(t) <= 80 and t.endswith(":"):
        return 2
    return None


def load_docx_with_structure(input_docx: Path) -> List[Dict[str, Any]]:
    """
    Load .docx and return paragraph-level structured rows:
    [{text, is_heading, heading_level, style_name}].
    """
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
        level = _heading_level(style_name)
        if level is None:
            level = _infer_heading_level_from_text(text)
        rows.append(
            {
                "text": text,
                "is_heading": level is not None,
                "heading_level": level,
                "style_name": style_name,
            }
        )
    return rows


def build_section_blocks(rows: Sequence[Dict[str, Any]]) -> List[SectionBlock]:
    """
    Convert paragraph rows into heading-scoped SectionBlock entries.

    Mapping:
      Heading 1 -> section_title
      Heading 2 -> subsection_title
      Heading 3+ -> subsubsection_title (latest)
    """
    blocks: List[SectionBlock] = []
    h1: Optional[str] = None
    h2: Optional[str] = None
    h3: Optional[str] = None
    buffer: List[str] = []

    def flush() -> None:
        nonlocal buffer
        if not buffer:
            return
        text = "\n\n".join(buffer).strip()
        if text:
            blocks.append(
                SectionBlock(
                    section_title=h1,
                    subsection_title=h2,
                    subsubsection_title=h3,
                    content=text,
                )
            )
        buffer = []

    for row in rows:
        if row["is_heading"]:
            flush()
            level = int(row.get("heading_level") or 0)
            heading_text = row["text"].strip()
            if level == 1:
                h1 = heading_text
                h2 = None
                h3 = None
            elif level == 2:
                h2 = heading_text
                h3 = None
            else:
                h3 = heading_text
            continue

        buffer.append(row["text"])

    flush()
    return blocks


def _build_heading_entries(blocks: Sequence[SectionBlock]) -> List[HeadingEntry]:
    """Create heading entries preserving source heading text and attached content."""
    entries: List[HeadingEntry] = []
    for b in blocks:
        for heading in (b.section_title, b.subsection_title, b.subsubsection_title):
            if not heading:
                continue
            nh = _normalize_key(heading)
            if not nh:
                continue
            entries.append(HeadingEntry(raw_heading=heading, normalized_heading=nh, content=b.content))
    return entries


def _collect_heading_text(entries: Sequence[HeadingEntry]) -> Dict[str, str]:
    """Create normalized-heading -> merged-content map."""
    merged: Dict[str, List[str]] = {}
    for e in entries:
        merged.setdefault(e.normalized_heading, []).append(e.content)
    return {k: "\n\n".join(v).strip() for k, v in merged.items() if v}


def _find_entries_by_heading_keywords(
    entries: Sequence[HeadingEntry],
    keyword_groups: Sequence[Sequence[str]],
    exclude_groups: Optional[Sequence[Sequence[str]]] = None,
) -> List[HeadingEntry]:
    """Find heading entries matching any include-group and no exclude-group."""
    results: List[HeadingEntry] = []
    for e in entries:
        key = e.normalized_heading
        include_hit = any(all(tok in key for tok in group) for group in keyword_groups)
        if not include_hit:
            continue
        if exclude_groups and any(all(tok in key for tok in group) for group in exclude_groups):
            continue
        results.append(e)

    # Sort by specificity (more tokens matched first), then shorter heading.
    def _score(x: HeadingEntry) -> Tuple[int, int]:
        max_tok = 0
        for g in keyword_groups:
            if all(tok in x.normalized_heading for tok in g):
                max_tok = max(max_tok, len(g))
        return (max_tok, -len(x.normalized_heading))

    results.sort(key=_score, reverse=True)
    return results


def _merge_entry_contents(entries: Sequence[HeadingEntry]) -> str:
    if not entries:
        return ""
    seen = set()
    out: List[str] = []
    for e in entries:
        k = _normalize_key(e.content)
        if k and k not in seen:
            out.append(e.content)
            seen.add(k)
    return "\n\n".join(out).strip()


def _find_by_heading_keywords(
    heading_to_text: Dict[str, str],
    keyword_groups: Sequence[Sequence[str]],
) -> Optional[str]:
    """Backward-compatible map lookup for simple extraction cases."""
    for key, text in heading_to_text.items():
        for group in keyword_groups:
            if all(g in key for g in group):
                return text
    return None


def _extract_first_line(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    for line in text.splitlines():
        line = line.strip(" -•\t")
        if line:
            return line
    return None


def _extract_bullets_or_sentences(text: Optional[str], max_items: int = 12) -> List[str]:
    if not text:
        return []

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    # Bullet/numbered list extraction first.
    bullet_items: List[str] = []
    bullet_re = re.compile(r"^(?:[-*•]|\d+[\.)])\s+(.+)$")
    for ln in lines:
        m = bullet_re.match(ln)
        if m:
            item = m.group(1).strip()
            if item:
                bullet_items.append(item)

    if bullet_items:
        deduped: List[str] = []
        seen = set()
        for it in bullet_items:
            key = _normalize_key(it)
            if key and key not in seen:
                deduped.append(it)
                seen.add(key)
        return deduped[:max_items]

    # Fallback: sentence and compound-idea splitting.
    raw_text = " ".join(lines)
    raw_text = raw_text.replace("” “", "”. “")
    raw_text = re.sub(r"\s+(?=(Begins|Moves|Uses|Typical|Core|Pedagogical|What)\b)", ". ", raw_text)
    raw_text = raw_text.replace(";", ". ")

    raw = re.split(r"(?<=[.!?])\s+", raw_text)
    candidates = [s.strip() for s in raw if len(s.strip()) >= 8]

    deduped2: List[str] = []
    seen2 = set()
    for c in candidates:
        key = _normalize_key(c)
        if key and key not in seen2:
            deduped2.append(c)
            seen2.add(key)
    return deduped2[:max_items]


def _dedupe_list(items: Sequence[str], used_norm: Optional[set] = None, max_items: Optional[int] = None) -> List[str]:
    out: List[str] = []
    local_seen = set()
    external = used_norm if used_norm is not None else set()
    for item in items:
        norm = _normalize_key(item)
        if not norm or norm in local_seen or norm in external:
            continue
        out.append(item.strip())
        local_seen.add(norm)
        if used_norm is not None:
            used_norm.add(norm)
        if max_items is not None and len(out) >= max_items:
            break
    return out


def _extract_key_value_fields(blocks: Sequence[SectionBlock]) -> Dict[str, str]:
    """Parse explicit key-value lines like 'Name: Alex Martinez'."""
    kv: Dict[str, str] = {}
    key_val_re = re.compile(r"^\s*([A-Za-z][A-Za-z0-9\s/&\-]{1,60})\s*:\s*(.+?)\s*$")
    for b in blocks:
        for line in b.content.splitlines():
            m = key_val_re.match(line)
            if not m:
                continue
            key = _normalize_key(m.group(1))
            val = m.group(2).strip()
            if key and val and key not in kv:
                kv[key] = val
    return kv


def _reduce_archetype_label(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    line = _extract_first_line(text)
    if not line:
        return None

    # Example: "... operates as a Diplomatic Gatekeeper—..."
    m = re.search(r"\bas\s+(?:a|an)\s+([A-Z][A-Za-z\- ]{2,80}?)(?:[—\-,]|\s+who\b|\.)", line)
    if m:
        return m.group(1).strip()

    # Example: "Archetype: Diplomatic Gatekeeper"
    m2 = re.search(r"(?i)archetype\s*:\s*(.+)$", line)
    if m2:
        candidate = m2.group(1).strip()
        candidate = re.split(r"[—\-,:]", candidate)[0].strip()
        if candidate:
            return candidate

    if len(line) <= 80:
        return line
    return None


def _is_age_like(value: str) -> bool:
    v = _normalize_key(value)
    return bool(re.match(r"^\d{1,3}$", v) or re.match(r"^age\s*\d{1,3}$", v))


def _set_debug(debug: Dict[str, Dict[str, Any]], field: str, source_heading: Optional[str], method: str, confidence: float) -> None:
    debug[field] = {
        "source_heading": source_heading,
        "extraction_method": method,
        "confidence": round(float(confidence), 3),
    }


def _extract_labeled_sections(text: str, label_aliases: Dict[str, List[str]]) -> Dict[str, str]:
    """
    Extract line-oriented labeled sections from a block.
    Example labels: baseline/elevated/reflective/defensive/hypothetical.
    """
    result: Dict[str, List[str]] = {k: [] for k in label_aliases}
    if not text:
        return {k: "" for k in label_aliases}

    norm_alias_to_key: Dict[str, str] = {}
    for key, aliases in label_aliases.items():
        for a in aliases:
            norm_alias_to_key[_normalize_key(a)] = key

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    current: Optional[str] = None

    for line in lines:
        line_norm = _normalize_key(line)
        matched_key: Optional[str] = None
        for alias_norm, key in norm_alias_to_key.items():
            if line_norm.startswith(alias_norm):
                matched_key = key
                break

        if matched_key:
            current = matched_key
            after = re.sub(r"^[^:]{1,80}:\s*", "", line).strip()
            if after and after != line:
                result[current].append(after)
            continue

        if current:
            result[current].append(line)

    return {k: "\n".join(v).strip() for k, v in result.items()}


def _split_sentences(text: str) -> List[str]:
    if not text:
        return []
    t = " ".join(line.strip() for line in text.splitlines() if line.strip())
    t = re.sub(r"\s+", " ", t).strip()
    if not t:
        return []
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", t) if len(s.strip()) >= 8]


def _select_sentences_by_keywords(text: str, keywords: Sequence[str], max_items: int = 6) -> List[str]:
    sentences = _split_sentences(text)
    out: List[str] = []
    for s in sentences:
        sn = _normalize_key(s)
        if any(k in sn for k in keywords):
            out.append(s)
    return _dedupe_list(out, None, max_items)


def _warn_if_missing(field_name: str, value: Any) -> None:
    missing = value is None or value == [] or value == {}
    if missing:
        logging.warning("Could not confidently extract '%s'; leaving null/empty.", field_name)


def extract_persona_identity(
    blocks: Sequence[SectionBlock],
    heading_entries: Sequence[HeadingEntry],
    heading_to_text: Dict[str, str],
    persona_name_hint: Optional[str],
    debug: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """Extract top-level persona identity fields with strict scalar rules."""
    kv = _extract_key_value_fields(blocks)

    role_entries = _find_entries_by_heading_keywords(
        heading_entries,
        [
            ("role", "institutional", "position"),
            ("role", "title"),
            ("institutional", "position"),
        ],
    )
    role_text = _merge_entry_contents(role_entries)

    rel_entries = _find_entries_by_heading_keywords(
        heading_entries,
        [
            ("relationship", "harbortown"),
            ("lived", "relationship", "place"),
            ("relationship", "place"),
        ],
    )
    rel_text = _merge_entry_contents(rel_entries)

    archetype_entries = _find_entries_by_heading_keywords(
        heading_entries,
        [
            ("interview", "personality", "archetype"),
            ("personality", "archetype"),
            ("archetype",),
        ],
    )
    archetype_text = _merge_entry_contents(archetype_entries)

    summary_entries = _find_entries_by_heading_keywords(
        heading_entries,
        [
            ("short", "identity", "summary"),
            ("identity", "summary"),
        ],
    )
    summary_text = _merge_entry_contents(summary_entries)

    persona_name = persona_name_hint
    if persona_name:
        _set_debug(debug, "persona_name", None, "cli_override", 1.0)
    if not persona_name:
        for key in ("name", "persona name", "stakeholder name"):
            if key in kv and not _is_age_like(kv[key]):
                persona_name = kv[key]
                _set_debug(debug, "persona_name", "key_value", f"kv:{key}", 0.98)
                break

    role_title: Optional[str] = None
    for key in ("role title", "job title", "title", "position"):
        v = kv.get(key)
        if v and not _is_age_like(v):
            role_title = v
            _set_debug(debug, "role_title", "key_value", f"kv:{key}", 0.96)
            break
    if role_title is None and role_text:
        role_title = _extract_first_line(role_text)
        if role_title and not _is_age_like(role_title):
            src = role_entries[0].raw_heading if role_entries else None
            _set_debug(debug, "role_title", src, "heading_first_line", 0.72)

    department_or_affiliation: Optional[str] = None
    for key in ("department", "department or affiliation", "affiliation", "office", "agency"):
        v = kv.get(key)
        if v and not _is_age_like(v):
            department_or_affiliation = v
            _set_debug(debug, "department_or_affiliation", "key_value", f"kv:{key}", 0.96)
            break

    if department_or_affiliation is None and role_text:
        lines = [ln.strip(" -•\t") for ln in role_text.splitlines() if ln.strip()]
        for ln in lines:
            if _is_age_like(ln):
                continue
            if role_title and _normalize_key(ln) == _normalize_key(role_title):
                continue
            if any(tok in _normalize_key(ln) for tok in ("department", "office", "agency", "division", "planning")):
                department_or_affiliation = ln
                src = role_entries[0].raw_heading if role_entries else None
                _set_debug(debug, "department_or_affiliation", src, "role_section_line_pick", 0.68)
                break

    archetype = _reduce_archetype_label(archetype_text)
    if archetype:
        src = archetype_entries[0].raw_heading if archetype_entries else None
        _set_debug(debug, "archetype", src, "archetype_label_reduce", 0.9)

    short_identity_summary = _extract_first_line(summary_text)
    if short_identity_summary:
        src = summary_entries[0].raw_heading if summary_entries else None
        _set_debug(debug, "short_identity_summary", src, "summary_heading_first_line", 0.78)

    lived_relationship_to_place = _extract_first_line(rel_text)
    if lived_relationship_to_place:
        src = rel_entries[0].raw_heading if rel_entries else None
        _set_debug(debug, "lived_relationship_to_place", src, "relationship_heading_first_line", 0.86)

    result = {
        "persona_name": persona_name,
        "role_title": role_title,
        "department_or_affiliation": department_or_affiliation,
        "archetype": archetype,
        "short_identity_summary": short_identity_summary,
        "lived_relationship_to_place": lived_relationship_to_place,
    }

    for k, v in result.items():
        _warn_if_missing(k, v)

    return result


def extract_behavior_rules(
    blocks: Sequence[SectionBlock],
    heading_entries: Sequence[HeadingEntry],
    heading_to_text: Dict[str, str],
    debug: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """Extract style and behavior rules with section-specific non-overlapping logic."""
    kv = _extract_key_value_fields(blocks)

    tone_entries = _find_entries_by_heading_keywords(
        heading_entries,
        [("tone",), ("default", "tone"), ("conversational", "style")],
        exclude_groups=[("speech",), ("guardrails",)],
    )
    speech_entries = _find_entries_by_heading_keywords(
        heading_entries,
        [("speech", "patterns"), ("response", "pacing"), ("speech",)],
        exclude_groups=[("tone",), ("guardrails",)],
    )
    vocab_entries = _find_entries_by_heading_keywords(
        heading_entries,
        [("vocabulary", "preferences"), ("vocabulary",)],
    )
    avoid_entries = _find_entries_by_heading_keywords(
        heading_entries,
        [("avoid", "terms"), ("avoid",)],
    )
    disclosure_entries = _find_entries_by_heading_keywords(
        heading_entries,
        [("disclosure", "logic"), ("disclosure", "modes"), ("disclosure",)],
    )

    tone_text = _merge_entry_contents(tone_entries)
    speech_text = _merge_entry_contents(speech_entries)
    vocab_text = _merge_entry_contents(vocab_entries)
    avoid_text = _merge_entry_contents(avoid_entries)
    disclosure_text = _merge_entry_contents(disclosure_entries)

    used_shared = set()
    tone_rules = _dedupe_list(_extract_bullets_or_sentences(tone_text, max_items=20), used_shared, max_items=12)
    speech_patterns = _dedupe_list(
        _extract_bullets_or_sentences(speech_text, max_items=20),
        used_shared,
        max_items=12,
    )

    vocabulary_preferences = _dedupe_list(_extract_bullets_or_sentences(vocab_text, max_items=24), None, max_items=20)
    avoid_terms = _dedupe_list(_extract_bullets_or_sentences(avoid_text, max_items=24), None, max_items=20)

    default_response_mode = None
    for key in ("default response mode", "response mode", "default stance", "baseline mode"):
        if key in kv and len(kv[key]) <= 120:
            default_response_mode = kv[key]
            _set_debug(debug, "default_response_mode", "key_value", f"kv:{key}", 0.95)
            break
    if default_response_mode is None:
        drm_entries = _find_entries_by_heading_keywords(
            heading_entries,
            [("default", "response", "mode"), ("response", "mode"), ("baseline", "mode")],
        )
        drm_text = _merge_entry_contents(drm_entries)
        candidate = _extract_first_line(drm_text)
        if candidate and len(candidate) <= 120:
            default_response_mode = candidate
            src = drm_entries[0].raw_heading if drm_entries else None
            _set_debug(debug, "default_response_mode", src, "heading_first_line", 0.7)

    # Disclosure modes (explicit).
    disclosure_modes = {
        "baseline": [],
        "elevated": [],
        "reflective": [],
        "defensive": [],
        "hypothetical": [],
    }

    mode_aliases = {
        "baseline": ["baseline", "baseline mode", "mode baseline"],
        "elevated": ["elevated", "elevated mode"],
        "reflective": ["reflective", "reflective mode"],
        "defensive": ["defensive", "defensive mode"],
        "hypothetical": ["hypothetical", "hypothetical mode"],
    }
    mode_sections = _extract_labeled_sections(disclosure_text, mode_aliases)
    for mode in disclosure_modes:
        mode_heading_entries = _find_entries_by_heading_keywords(
            heading_entries,
            [("disclosure", mode), (mode, "mode")],
        )
        mode_heading_text = _merge_entry_contents(mode_heading_entries)
        source_text = mode_sections.get(mode) or mode_heading_text
        disclosure_modes[mode] = _dedupe_list(_extract_bullets_or_sentences(source_text, max_items=12), None, max_items=8)

        # Keyword fallback from disclosure-logic section if explicit labels are missing.
        if not disclosure_modes[mode] and disclosure_text:
            mode_keywords = {
                "baseline": ["baseline", "default", "public", "procedural"],
                "elevated": ["elevated", "deeper", "more detail"],
                "reflective": ["reflective", "ethical", "moral", "tension"],
                "defensive": ["defensive", "retreat", "procedural language", "guarded"],
                "hypothetical": ["hypothetical", "if", "scenario", "thought exercise"],
            }
            disclosure_modes[mode] = _select_sentences_by_keywords(
                disclosure_text,
                mode_keywords[mode],
                max_items=4,
            )

    # Behavioral rule extraction using explicit labels + dedicated headings.
    behavior_aliases = {
        "what_increases_openness": ["what increases openness", "increases openness", "openness triggers"],
        "retreat_triggers_procedural_language": [
            "retreat triggers procedural language",
            "causes retreat into procedural language",
            "retreat into procedural language",
        ],
        "will_not_explicitly_say": ["will not explicitly say", "wont explicitly say", "will not say"],
        "react_to_confrontational_questions": ["react to confrontational questions", "confrontational questions"],
        "react_to_respectful_context_aware_questions": [
            "react to respectful and context-aware questions",
            "respectful and context-aware questions",
            "respectful questions",
        ],
    }
    behavior_sections = _extract_labeled_sections(disclosure_text, behavior_aliases)

    behavioral_rules: Dict[str, List[str]] = {}
    for key, aliases in behavior_aliases.items():
        heading_hits = _find_entries_by_heading_keywords(
            heading_entries,
            [tuple(_normalize_key(a).split()) for a in aliases],
        )
        heading_text = _merge_entry_contents(heading_hits)
        source_text = behavior_sections.get(key) or heading_text
        behavioral_rules[key] = _dedupe_list(_extract_bullets_or_sentences(source_text, max_items=12), None, max_items=8)

        # Fallback: use disclosure logic sentences filtered by intent keywords.
        if not behavioral_rules[key] and disclosure_text:
            keyword_map = {
                "what_increases_openness": ["openness", "opens", "trust", "context-aware", "respectful"],
                "retreat_triggers_procedural_language": ["retreat", "procedural", "guarded", "shorter", "withhold"],
                "will_not_explicitly_say": ["will not", "never", "does not", "not explicitly", "wont"],
                "react_to_confrontational_questions": ["confrontational", "aggressive", "accusatory", "hostile"],
                "react_to_respectful_context_aware_questions": ["respectful", "context-aware", "framing", "reflective listening"],
            }
            behavioral_rules[key] = _select_sentences_by_keywords(disclosure_text, keyword_map[key], max_items=5)

    result = {
        "tone_rules": tone_rules,
        "speech_patterns": speech_patterns,
        "vocabulary_preferences": vocabulary_preferences,
        "avoid_terms": avoid_terms,
        "default_response_mode": default_response_mode,
        "disclosure_modes": disclosure_modes,
        "behavioral_rules": behavioral_rules,
    }

    if tone_entries:
        _set_debug(debug, "tone_rules", tone_entries[0].raw_heading, "heading_section_list_extract", 0.9)
    if speech_entries:
        _set_debug(debug, "speech_patterns", speech_entries[0].raw_heading, "heading_section_list_extract", 0.9)
    if vocab_entries:
        _set_debug(debug, "vocabulary_preferences", vocab_entries[0].raw_heading, "heading_section_list_extract", 0.88)
    if avoid_entries:
        _set_debug(debug, "avoid_terms", avoid_entries[0].raw_heading, "heading_section_list_extract", 0.86)
    if disclosure_entries:
        _set_debug(debug, "disclosure_modes", disclosure_entries[0].raw_heading, "labeled_mode_extract", 0.8)
        _set_debug(debug, "behavioral_rules", disclosure_entries[0].raw_heading, "labeled_behavior_extract", 0.78)

    for k in [
        "tone_rules",
        "speech_patterns",
        "vocabulary_preferences",
        "avoid_terms",
        "default_response_mode",
    ]:
        _warn_if_missing(k, result[k])

    return result


def extract_knowledge_tiers(
    heading_entries: Sequence[HeadingEntry],
    heading_to_text: Dict[str, str],
    debug: Dict[str, Dict[str, Any]],
) -> Dict[str, List[str]]:
    """
    Extract knowledge tiers.

    If dossier has explicit tier sections, parse from them.
    Otherwise keep empty lists and warn.
    """
    inventory_text = _find_by_heading_keywords(
        heading_to_text,
        [
            ("knowledge", "inventory"),
            ("knowledge",),
        ],
    )

    t1_entries = _find_entries_by_heading_keywords(heading_entries, [("tier", "1"), ("public",)])
    t2_entries = _find_entries_by_heading_keywords(heading_entries, [("tier", "2"), ("contextual",)])
    t3_entries = _find_entries_by_heading_keywords(heading_entries, [("tier", "3"), ("sensitive",)])

    tiers = {
        "tier_1_public": _dedupe_list(_extract_bullets_or_sentences(_merge_entry_contents(t1_entries), max_items=30), None, 25),
        "tier_2_contextual": _dedupe_list(_extract_bullets_or_sentences(_merge_entry_contents(t2_entries), max_items=30), None, 25),
        "tier_3_sensitive": _dedupe_list(_extract_bullets_or_sentences(_merge_entry_contents(t3_entries), max_items=30), None, 25),
    }

    # If explicit tiers missing, fallback to general inventory as tier_1_public.
    if not any(tiers.values()) and inventory_text:
        tiers["tier_1_public"] = _dedupe_list(_extract_bullets_or_sentences(inventory_text, max_items=25), None, 25)

    if t1_entries:
        _set_debug(debug, "knowledge_tiers.tier_1_public", t1_entries[0].raw_heading, "tier_heading_extract", 0.9)
    if t2_entries:
        _set_debug(debug, "knowledge_tiers.tier_2_contextual", t2_entries[0].raw_heading, "tier_heading_extract", 0.9)
    if t3_entries:
        _set_debug(debug, "knowledge_tiers.tier_3_sensitive", t3_entries[0].raw_heading, "tier_heading_extract", 0.9)

    _warn_if_missing("knowledge_tiers", tiers if any(tiers.values()) else {})
    return tiers


def extract_guardrails(
    heading_entries: Sequence[HeadingEntry],
    heading_to_text: Dict[str, str],
    debug: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """Extract guardrails and ethics-related fields."""
    guardrail_entries = _find_entries_by_heading_keywords(
        heading_entries,
        [("design", "guardrails"), ("guardrails",)],
        exclude_groups=[("tone",), ("speech",)],
    )
    ethical_entries = _find_entries_by_heading_keywords(heading_entries, [("ethical", "tensions"), ("ethical", "tension")])
    tensions_entries = _find_entries_by_heading_keywords(heading_entries, [("core", "tensions")])

    relationship_entries = _find_entries_by_heading_keywords(
        heading_entries,
        [("relationship", "map"), ("stakeholder", "map"), ("institutional", "relationships")],
    )
    assessment_entries = _find_entries_by_heading_keywords(heading_entries, [("assessment", "hooks"), ("assessment", "criteria")])
    simulation_entries = _find_entries_by_heading_keywords(heading_entries, [("simulation", "function")])

    guardrail_text = _merge_entry_contents(guardrail_entries)
    ethical_text = _merge_entry_contents(ethical_entries)
    tensions_text = _merge_entry_contents(tensions_entries)
    relationship_text = _merge_entry_contents(relationship_entries)
    assessment_text = _merge_entry_contents(assessment_entries)
    simulation_text = _merge_entry_contents(simulation_entries)

    used = set()
    guardrail_list = _dedupe_list(_extract_bullets_or_sentences(guardrail_text, max_items=30), used, 20)
    ethical_list = _dedupe_list(_extract_bullets_or_sentences(ethical_text, max_items=30), used, 20)
    core_list = _dedupe_list(_extract_bullets_or_sentences(tensions_text, max_items=30), used, 20)

    simulation_function = _extract_first_line(simulation_text)
    if simulation_function and len(simulation_function) > 180:
        simulation_function = None

    # Fallbacks for less-structured dossiers.
    if not simulation_function:
        sim_fallback = _find_by_heading_keywords(
            heading_to_text,
            [("simulation", "function"), ("simulation",)],
        )
        candidate = _extract_first_line(sim_fallback)
        if candidate and len(candidate) <= 180:
            simulation_function = candidate
            _set_debug(debug, "simulation_function", "fallback_heading_match", "map_heading_first_line", 0.6)

    if not tensions_entries:
        tension_fallback_entries = _find_entries_by_heading_keywords(
            heading_entries,
            [("defining", "tension"), ("tension",)],
            exclude_groups=[("ethical", "tension")],
        )
        if tension_fallback_entries:
            core_list = _dedupe_list(
                _extract_bullets_or_sentences(_merge_entry_contents(tension_fallback_entries), max_items=24),
                used,
                20,
            )
            _set_debug(
                debug,
                "core_tensions",
                tension_fallback_entries[0].raw_heading,
                "defining_tension_fallback_extract",
                0.65,
            )

    result = {
        "core_tensions": core_list,
        "guardrails": guardrail_list,
        "ethical_tensions": ethical_list,
        "relationship_map": _dedupe_list(_extract_bullets_or_sentences(relationship_text, max_items=20), None, 20),
        "assessment_hooks": _dedupe_list(_extract_bullets_or_sentences(assessment_text, max_items=20), None, 20),
        "simulation_function": simulation_function,
    }

    if guardrail_entries:
        _set_debug(debug, "guardrails", guardrail_entries[0].raw_heading, "guardrail_heading_extract", 0.9)
    if ethical_entries:
        _set_debug(debug, "ethical_tensions", ethical_entries[0].raw_heading, "ethical_heading_extract", 0.9)
    if tensions_entries:
        _set_debug(debug, "core_tensions", tensions_entries[0].raw_heading, "core_tension_heading_extract", 0.9)
    if relationship_entries:
        _set_debug(debug, "relationship_map", relationship_entries[0].raw_heading, "relationship_heading_extract", 0.78)
    if assessment_entries:
        _set_debug(debug, "assessment_hooks", assessment_entries[0].raw_heading, "assessment_heading_extract", 0.84)
    if simulation_entries and simulation_function:
        _set_debug(debug, "simulation_function", simulation_entries[0].raw_heading, "simulation_heading_first_line", 0.84)

    for k, v in result.items():
        _warn_if_missing(k, v)

    return result


def build_persona_config(
    blocks: Sequence[SectionBlock],
    persona_name_hint: Optional[str] = None,
) -> Dict[str, Any]:
    """Build final structured persona configuration from extracted dossier blocks."""
    heading_entries = _build_heading_entries(blocks)
    heading_to_text = _collect_heading_text(heading_entries)
    extraction_debug: Dict[str, Dict[str, Any]] = {}

    identity = extract_persona_identity(
        blocks,
        heading_entries,
        heading_to_text,
        persona_name_hint=persona_name_hint,
        debug=extraction_debug,
    )
    behavior = extract_behavior_rules(
        blocks,
        heading_entries,
        heading_to_text,
        debug=extraction_debug,
    )
    tiers = extract_knowledge_tiers(heading_entries, heading_to_text, debug=extraction_debug)
    guardrails = extract_guardrails(heading_entries, heading_to_text, debug=extraction_debug)

    config: Dict[str, Any] = {
        # Identity
        "persona_name": identity["persona_name"],
        "role_title": identity["role_title"],
        "department_or_affiliation": identity["department_or_affiliation"],
        "archetype": identity["archetype"],
        "short_identity_summary": identity["short_identity_summary"],
        "lived_relationship_to_place": identity["lived_relationship_to_place"],
        # Behavioral style
        "tone_rules": behavior["tone_rules"],
        "speech_patterns": behavior["speech_patterns"],
        "vocabulary_preferences": behavior["vocabulary_preferences"],
        "avoid_terms": behavior["avoid_terms"],
        "default_response_mode": behavior["default_response_mode"],
        "disclosure_modes": behavior["disclosure_modes"],
        "behavioral_rules": behavior["behavioral_rules"],
        # Governance/ethics constraints
        "guardrails": guardrails["guardrails"],
        "ethical_tensions": guardrails["ethical_tensions"],
        "core_tensions": guardrails["core_tensions"],
        # Knowledge
        "knowledge_tiers": tiers,
        # Stakeholder/runtime support
        "relationship_map": guardrails["relationship_map"],
        "assessment_hooks": guardrails["assessment_hooks"],
        "simulation_function": guardrails["simulation_function"],
        # Traceability (optional metadata)
        "metadata": {
            "source_type": "docx",
            "parser": "heading_aware_rule_based",
            "block_count": len(blocks),
            "extraction_policy": "no_invention_missing_as_null_or_empty",
            "extraction_debug": extraction_debug,
        },
    }

    return config


def save_persona_config(config: Dict[str, Any], output_path: Path) -> None:
    """Save persona config as pretty JSON."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build structured persona config from a dossier .docx")
    parser.add_argument("--input-docx", type=Path, required=True, help="Path to persona dossier .docx")
    parser.add_argument(
        "--output-json",
        type=Path,
        default=None,
        help="Output JSON path (default: <input_stem>_persona_config.json next to input)",
    )
    parser.add_argument("--persona-name", type=str, default=None, help="Optional override for persona_name")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    input_docx: Path = args.input_docx
    output_json: Path = (
        args.output_json
        if args.output_json is not None
        else input_docx.with_name(f"{input_docx.stem}_persona_config.json")
    )

    try:
        rows = load_docx_with_structure(input_docx)
        blocks = build_section_blocks(rows)
        if not blocks:
            raise ValueError("No content blocks extracted from dossier.")

        config = build_persona_config(blocks, persona_name_hint=args.persona_name)
        save_persona_config(config, output_json)

    except Exception as exc:
        raise SystemExit(f"Failed to build persona config: {exc}") from exc

    print("\nPersona config created successfully:")
    print(f"- Input: {input_docx}")
    print(f"- Output: {output_json}")

    print("\nExample run:")
    print(
        "python backend/build_persona_config.py --input-docx backend/persona_dossiers/planner_dossier.docx "
        "--output-json backend/persona_configs/planner_persona_config.json"
    )


if __name__ == "__main__":
    main()
