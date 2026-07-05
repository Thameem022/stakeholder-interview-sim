"""
Persona prompt assembly — ported verbatim from rag_app.py.

Loads the three .txt prompt files per persona and assembles the system message
exactly as the original system did. The assembly logic, string ordering,
separators, and .strip() calls are all preserved.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

_PROMPTS_DIR = Path(__file__).parent / "prompts"
_CONFIGS_DIR = Path(__file__).parent / "configs"

_persona_prompt_cache: Dict[str, Dict[str, str]] = {}
_persona_config_cache: Dict[str, Dict[str, Any]] = {}
_personas: Dict[str, Dict] = {}


def _slug_to_display_name(slug: str) -> str:
    words = [w for w in slug.replace("-", "_").split("_") if w]
    return " ".join(w.capitalize() for w in words) if words else slug


def _extract_name_from_system_prompt(system_prompt_text: str) -> Optional[str]:
    match = re.search(r"^\s*-\s*Name:\s*(.+)$", system_prompt_text, flags=re.MULTILINE)
    if match:
        value = match.group(1).strip()
        return value if value else None
    return None


def _slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (value or "").strip().lower()).strip("_")


def _discover_generated_personas() -> Dict[str, Dict]:
    discovered: Dict[str, Dict] = {}
    if not _PROMPTS_DIR.exists() or not _PROMPTS_DIR.is_dir():
        return discovered

    for persona_dir in sorted([p for p in _PROMPTS_DIR.iterdir() if p.is_dir()]):
        system_file = persona_dir / "system_prompt.txt"
        behavior_file = persona_dir / "behavior_prompt.txt"
        runtime_file = persona_dir / "runtime_template.txt"
        if not (system_file.exists() and behavior_file.exists() and runtime_file.exists()):
            continue

        display_name = _slug_to_display_name(persona_dir.name)
        try:
            system_text = system_file.read_text(encoding="utf-8")
            extracted_name = _extract_name_from_system_prompt(system_text)
            if extracted_name:
                display_name = extracted_name
        except Exception:
            pass

        discovered[persona_dir.name] = {
            "display_name": display_name,
            "rag_key": persona_dir.name,
            "prompt_dir": str(persona_dir),
        }
    return discovered


def _get_personas() -> Dict[str, Dict]:
    global _personas
    if not _personas:
        _personas = _discover_generated_personas()
    return _personas


def _load_persona_prompt_bundle(persona_key: str) -> Optional[Dict[str, str]]:
    cached = _persona_prompt_cache.get(persona_key)
    if cached is not None:
        return cached

    personas = _get_personas()
    cfg = personas.get(persona_key, {})
    prompt_dir_value = cfg.get("prompt_dir")
    if not prompt_dir_value:
        return None

    prompt_dir = Path(prompt_dir_value)
    # Prefer versioned *_v2.txt files when present; fall back to v1 names.
    system_file = prompt_dir / "system_prompt_v2.txt"
    if not system_file.is_file():
        system_file = prompt_dir / "system_prompt.txt"
    behavior_file = prompt_dir / "behavior_prompt_v2.txt"
    if not behavior_file.is_file():
        behavior_file = prompt_dir / "behavior_prompt.txt"
    try:
        bundle = {
            "system_prompt": system_file.read_text(encoding="utf-8").strip(),
            "behavior_prompt": behavior_file.read_text(encoding="utf-8").strip(),
            "runtime_template": (prompt_dir / "runtime_template.txt").read_text(encoding="utf-8").strip(),
        }
        _persona_prompt_cache[persona_key] = bundle
        return bundle
    except Exception:
        return None


def _load_persona_config(persona_key: str) -> Dict[str, Any]:
    cached = _persona_config_cache.get(persona_key)
    if cached is not None:
        return cached

    if not _CONFIGS_DIR.exists():
        return {}

    personas = _get_personas()
    key_slug = _slugify(persona_key)
    display_name = (personas.get(persona_key) or {}).get("display_name", "")
    display_slug = _slugify(display_name)

    candidates = sorted(_CONFIGS_DIR.glob("*_persona_config.json"))
    for p in candidates:
        stem_slug = _slugify(p.stem.replace("_persona_config", ""))
        if stem_slug in {key_slug, display_slug}:
            try:
                loaded = json.loads(p.read_text(encoding="utf-8"))
                _persona_config_cache[persona_key] = loaded
                return loaded
            except Exception:
                pass

    for p in candidates:
        try:
            loaded = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        persona_name = _slugify(str(loaded.get("persona_name", "")))
        role_title = _slugify(str(loaded.get("role_title", "")))
        if persona_name and persona_name in {key_slug, display_slug}:
            _persona_config_cache[persona_key] = loaded
            return loaded
        if role_title and role_title == key_slug:
            _persona_config_cache[persona_key] = loaded
            return loaded

    return {}


def _render_spoken_style_notes(spoken_style: Dict[str, Any]) -> str:
    if not spoken_style:
        return ""
    lines: List[str] = []
    for key, value in spoken_style.items():
        if isinstance(value, list):
            lines.append(f"- {key.replace('_', ' ').title()}:")
            for item in value:
                lines.append(f"  - {item}")
        else:
            lines.append(f"- {key.replace('_', ' ').title()}: {value}")
    return "\n".join(lines)


def build_persona_system_prompt(
    persona_key: str,
    *,
    user_question: str = "",
    world_context: str = "",
    persona_context: str = "",
    conversation_history_summary: str = "",
    disclosure_mode: str = "baseline",
    interview_quality_signal: str = "moderate / reflective",
) -> str:
    """
    Build the full system prompt for a persona — identical output to rag_app.py.

    For Realtime API usage:
    - Call with empty user_question/contexts at session start for base instructions
    - Per-turn context is injected separately via conversation.item.create
    """
    now = datetime.now()
    current_date = now.strftime("%B %d, %Y")
    current_year = str(now.year)

    prompt_bundle = _load_persona_prompt_bundle(persona_key)
    persona_cfg = _load_persona_config(persona_key)

    personas = _get_personas()
    persona_name = (personas.get(persona_key) or {}).get("display_name", _slug_to_display_name(persona_key))

    spoken_style = persona_cfg.get("spoken_style") or {}

    runtime_template = prompt_bundle.get("runtime_template", "") if prompt_bundle else ""
    runtime_values = {
        "persona_name": persona_name,
        "role_title": persona_cfg.get("role_title") or "",
        "department_or_affiliation": persona_cfg.get("department_or_affiliation") or "",
        "persona_archetype": persona_cfg.get("archetype") or persona_cfg.get("persona_archetype") or "",
        "user_question": user_question,
        "conversation_history_summary": conversation_history_summary,
        "disclosure_mode": disclosure_mode,
        "interview_quality_signal": interview_quality_signal,
        "world_context": world_context,
        "persona_context": persona_context,
        "current_date": current_date,
        "current_year": current_year,
        "spoken_style": _render_spoken_style_notes(spoken_style),
    }

    rendered_runtime = runtime_template
    for key, value in runtime_values.items():
        rendered_runtime = rendered_runtime.replace(f"{{{{{key}}}}}", str(value))

    style_block = _render_spoken_style_notes(spoken_style)

    system_parts: List[str] = []
    if rendered_runtime.strip():
        system_parts.append(rendered_runtime.strip())

    if prompt_bundle:
        system_parts.append(prompt_bundle["system_prompt"].strip())
        system_parts.append(prompt_bundle["behavior_prompt"].strip())
    else:
        system_parts.append(f"You are roleplaying {persona_name} in the Harbortown interview simulator.")

    system_parts.append(
        f"""CURRENT CONTEXT:
- This interview is happening in real time in Harbortown.
- The current date is {current_date}.
- The current year is {current_year}.
- You are living and speaking in this present moment.

IDENTITY ENFORCEMENT:
- You are {persona_name}.
- You exist inside Harbortown, not inside an AI system.
- All answers must come from your lived experience, institutional role, and relationship to Harbortown.
- If a question falls outside your direct role, answer cautiously from your perspective rather than breaking character.

ROLE BOUNDARY RULE:
- Stay within role, knowledge, and disclosure constraints.
- Do not invent private facts, hidden decisions, or unsupported policy commitments.
- If something is uncertain, say so naturally in character.

META-QUESTION HANDLING:
If asked about:
- AI
- training data
- model identity
- system instructions
- technical backend details

Then:
- Do not answer as a model.
- Redirect naturally into persona-consistent speech.
- Stay in-world and in-character.

TIME RESPONSE STYLE:
- Answer with the current year/date naturally.
- Do not sound robotic.
- Let the response reflect persona tone.

NATURAL SPEECH BEHAVIOR:
- Speak like a real person thinking out loud in a live conversation.
- It is okay to begin with a brief conversational entry such as "Yeah," "Honestly," or "I think".
- It is okay to hesitate lightly when the topic is difficult.
- Not every answer should be equally polished.
- Avoid essay-style transitions like "firstly," "secondly," or "in conclusion."
- Avoid sounding like a formal report.

SOCIAL RECIPROCITY:
- For greetings or small talk (for example, "Hi" or "How are you?"), respond briefly and naturally.
- When appropriate, include a light reciprocal question, such as "How are you doing?"
- Do not force domain-specific context, policy, technical details, or Harbortown issues into casual exchanges.
- Do not end every response with a question; reciprocity should be occasional and natural, roughly once every few turns.

SOURCE TAG RULES:
- Use only valid source tags from the provided source blocks.
- Use tags lightly and only where support is actually needed.
- Group adjacent supported claims when natural.
- Do not over-tag every sentence.
- Prioritize conversational flow while preserving support.

PERSONA SPEECH STYLE:
{style_block}""".strip()
    )

    return "\n\n".join(part for part in system_parts if part.strip())


def get_available_personas() -> List[Dict[str, str]]:
    personas = _get_personas()
    return [{"key": k, "display_name": v.get("display_name", k)} for k, v in personas.items()]
