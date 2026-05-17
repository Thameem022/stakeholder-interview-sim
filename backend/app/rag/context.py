"""
Persona dossier + facts chunking, ported verbatim from rag_engine.py.

These functions produce the *exact same chunk text* as the original system.
Only the embedding/retrieval transport changes (FAISS → pgvector + OpenAI).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

_CONFIGS_DIR = Path(__file__).parent.parent / "personas" / "configs"

_persona_record_cache: Optional[List[Dict[str, Any]]] = None
_persona_dossier_chunk_cache: Dict[str, List[Dict[str, Any]]] = {}
_persona_fact_cache: Dict[str, List[Dict[str, Any]]] = {}

_FACT_STOPLINES = {
    "core content",
    "high-quality indicators",
    "primary iqr dimensions tested",
    "what this evaluates",
    "common weakness",
}


def _slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (value or "").strip().lower()).strip("_")


def _normalize_text_for_dedupe(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _build_persona_records() -> List[Dict[str, Any]]:
    global _persona_record_cache
    if _persona_record_cache is not None:
        return _persona_record_cache

    records: List[Dict[str, Any]] = []
    for path in sorted(_CONFIGS_DIR.glob("*_persona_config.json")):
        try:
            config = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue

        file_slug = _slugify(path.stem.replace("_persona_config", ""))
        persona_name = str(config.get("persona_name") or "").strip()
        persona_slug = _slugify(persona_name)
        role_slug = _slugify(str(config.get("role_title") or ""))

        aliases = {file_slug}
        if persona_slug:
            aliases.add(persona_slug)
        if role_slug:
            aliases.add(role_slug)

        records.append(
            {
                "config_path": path,
                "config_slug": file_slug,
                "canonical_key": persona_slug or file_slug,
                "display_name": persona_name or file_slug.replace("_", " ").title(),
                "persona_name": persona_name or file_slug.replace("_", " ").title(),
                "aliases": aliases,
                "config": config,
            }
        )

    _persona_record_cache = records
    return records


def resolve_persona_record(persona: str) -> Dict[str, Any]:
    key = _slugify(persona)
    records = _build_persona_records()

    exact_file = next((r for r in records if r["config_slug"] == key), None)
    if exact_file:
        return exact_file

    alias_match = next((r for r in records if key in r["aliases"]), None)
    if alias_match:
        return alias_match

    raise RuntimeError(f"Could not resolve persona '{persona}' to a persona config")


def _append_dossier_chunk(
    target: List[Dict[str, Any]],
    persona_key: str,
    source_doc: str,
    version: str,
    section: str,
    text: Optional[str],
):
    body = (text or "").strip()
    if not body:
        return
    chunk_key = f"{persona_key}:{section}:{len(target) + 1}"
    target.append(
        {
            "source_id": chunk_key,
            "text": body,
            "metadata": {
                "persona_key": persona_key,
                "section": section,
                "source_doc": source_doc,
                "chunk_id": chunk_key,
                "version": version,
                "source_type": "persona_dossier",
            },
        }
    )


def _format_list_block(title: str, items: Sequence[Any]) -> str:
    values = [str(item).strip() for item in items if str(item).strip()]
    if not values:
        return ""
    return f"{title}:\n" + "\n".join(f"- {value}" for value in values)


def _format_dict_block(title: str, value: Dict[str, Any]) -> str:
    lines = []
    for key, item in value.items():
        if isinstance(item, list):
            block = _format_list_block(key.replace("_", " ").title(), item)
            if block:
                lines.append(block)
        elif isinstance(item, dict):
            nested = _format_dict_block(key.replace("_", " ").title(), item)
            if nested:
                lines.append(nested)
        else:
            text = str(item).strip()
            if text:
                lines.append(f"{key.replace('_', ' ').title()}: {text}")
    if not lines:
        return ""
    return f"{title}:\n" + "\n\n".join(lines)


def build_persona_dossier_chunks(persona: str) -> List[Dict[str, Any]]:
    record = resolve_persona_record(persona)
    persona_key = record["canonical_key"]
    cached = _persona_dossier_chunk_cache.get(persona_key)
    if cached is not None:
        return cached

    config = record["config"]
    source_doc = record["config_path"].name
    version = str(int(record["config_path"].stat().st_mtime))
    chunks: List[Dict[str, Any]] = []

    scalar_fields = {
        "identity_summary": config.get("short_identity_summary"),
        "lived_relationship_to_place": config.get("lived_relationship_to_place"),
        "archetype": config.get("archetype"),
        "simulation_function": config.get("simulation_function"),
    }
    for section, text in scalar_fields.items():
        _append_dossier_chunk(chunks, persona_key, source_doc, version, section, text)

    for section in [
        "tone_rules",
        "speech_patterns",
        "vocabulary_preferences",
        "avoid_terms",
        "guardrails",
        "ethical_tensions",
        "core_tensions",
        "relationship_map",
        "assessment_hooks",
    ]:
        block = _format_list_block(section.replace("_", " ").title(), config.get(section) or [])
        _append_dossier_chunk(chunks, persona_key, source_doc, version, section, block)

    knowledge_tiers = config.get("knowledge_tiers") or {}
    for tier_name, values in knowledge_tiers.items():
        block = _format_list_block(f"Knowledge Tier {tier_name}", values or [])
        _append_dossier_chunk(chunks, persona_key, source_doc, version, f"knowledge_{tier_name}", block)

    disclosure_modes = config.get("disclosure_modes") or {}
    for mode_name, mode_value in disclosure_modes.items():
        block = _format_dict_block(f"Disclosure Mode {mode_name}", mode_value or {})
        _append_dossier_chunk(chunks, persona_key, source_doc, version, f"disclosure_{mode_name}", block)

    behavioral_rules = config.get("behavioral_rules") or {}
    if behavioral_rules:
        block = _format_dict_block("Behavioral Rules", behavioral_rules)
        _append_dossier_chunk(chunks, persona_key, source_doc, version, "behavioral_rules", block)

    _persona_dossier_chunk_cache[persona_key] = chunks
    return chunks


def _fact_candidates_from_config(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    facts: List[Dict[str, Any]] = []

    def add_fact(text: Optional[str], category: str, field: str, priority: float = 0.0):
        value = (text or "").strip()
        if not value or value.lower().startswith("unknown "):
            return
        facts.append(
            {
                "text": value,
                "metadata": {
                    "category": category,
                    "field": field,
                    "priority": float(priority),
                    "source_type": "persona_fact",
                },
            }
        )

    add_fact(f"Name: {config.get('persona_name')}", "bio", "name", 1.0)
    add_fact(f"Role: {config.get('role_title')}", "bio", "role_title", 0.9)
    add_fact(
        f"Affiliation: {config.get('department_or_affiliation')}",
        "bio",
        "department_or_affiliation",
        0.8,
    )
    add_fact(config.get("archetype"), "bio", "archetype", 0.8)
    add_fact(config.get("short_identity_summary"), "claims", "identity_summary", 0.95)
    add_fact(config.get("lived_relationship_to_place"), "claims", "lived_relationship_to_place", 0.9)

    for item in config.get("core_tensions") or []:
        add_fact(item, "claims", "core_tensions", 0.88)

    for item in config.get("tone_rules") or []:
        add_fact(item, "preferences", "tone_rules", 0.7)

    for item in config.get("vocabulary_preferences") or []:
        add_fact(item, "preferences", "vocabulary_preferences", 0.68)

    for item in config.get("avoid_terms") or []:
        add_fact(item, "guardrails", "avoid_terms", 0.72)

    for item in config.get("guardrails") or []:
        add_fact(item, "guardrails", "guardrails", 0.8)

    for key, values in (config.get("behavioral_rules") or {}).items():
        for item in values or []:
            add_fact(item, "preferences", f"behavioral_rules.{key}", 0.66)

    for tier_name, values in (config.get("knowledge_tiers") or {}).items():
        for item in values or []:
            lower = item.strip().lower()
            if (
                lower.startswith("sic tier:")
                or lower.startswith("interview threshold:")
                or lower.startswith("developer stance:")
                or lower.startswith("owner stance:")
                or lower.startswith("planner stance:")
                or lower.startswith("resident stance:")
                or lower in _FACT_STOPLINES
            ):
                continue
            add_fact(item, "claims", f"knowledge_tiers.{tier_name}", 0.64)

    deduped: List[Dict[str, Any]] = []
    seen = set()
    for fact in facts:
        key = _normalize_text_for_dedupe(fact["text"])
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(fact)
    return deduped


def build_persona_facts(persona: str) -> List[Dict[str, Any]]:
    record = resolve_persona_record(persona)
    persona_key = record["canonical_key"]
    cached = _persona_fact_cache.get(persona_key)
    if cached is not None:
        return cached

    facts = _fact_candidates_from_config(record["config"])
    for idx, fact in enumerate(facts, start=1):
        fact["source_id"] = f"{persona_key}:fact:{idx}"
        fact["metadata"].update(
            {
                "persona_key": persona_key,
                "source_doc": record["config_path"].name,
                "chunk_id": fact["source_id"],
                "version": str(int(record["config_path"].stat().st_mtime)),
            }
        )

    _persona_fact_cache[persona_key] = facts
    return facts
