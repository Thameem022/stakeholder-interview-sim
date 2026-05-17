"""
build_persona_prompt.py

Generate runtime prompt templates from a structured persona config JSON.

Inputs:
- persona config JSON (raw or normalized)

Outputs (saved under persona_prompts/<persona_name_slug>/):
- system_prompt.txt
- behavior_prompt.txt
- runtime_template.txt

Example:
    /opt/anaconda3/envs/glob/bin/python backend/build_persona_prompt.py \
      --config-json backend/persona_configs/planner_persona_config.json \
      --output-root backend/persona_prompts
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


def _normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _slugify(value: str) -> str:
    s = _normalize_ws(value).lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = s.strip("_")
    return s or "persona"


def _clean_item(x: Any) -> Optional[str]:
    if x is None:
        return None
    s = _normalize_ws(str(x))
    if not s:
        return None
    s = s.strip("\"'“”‘’ ")
    if len(s) < 2:
        return None
    return s


def _listify(value: Any) -> List[str]:
    """Convert any list/string-ish value into clean unique bullet strings."""
    raw: List[str] = []
    if value is None:
        return []
    if isinstance(value, list):
        raw = [str(v) for v in value]
    elif isinstance(value, str):
        raw = [value]
    elif isinstance(value, dict):
        # flatten dict values conservatively
        for v in value.values():
            if isinstance(v, list):
                raw.extend(str(i) for i in v)
            elif isinstance(v, str):
                raw.append(v)
    else:
        raw = [str(value)]

    out: List[str] = []
    seen = set()
    for item in raw:
        cleaned = _clean_item(item)
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        out.append(cleaned)
        seen.add(key)
    return out


def _format_bullets(items: Sequence[str], indent: str = "- ") -> str:
    if not items:
        return "- (none provided)"
    return "\n".join(f"{indent}{i}" for i in items)


def _get_mode_sections(disclosure_modes: Dict[str, Any], mode: str) -> Dict[str, List[str]]:
    """Return normalized structured disclosure mode fields."""
    mode_data = disclosure_modes.get(mode, {})

    # structured schema expected
    if isinstance(mode_data, dict):
        return {
            "triggers": _listify(mode_data.get("triggers")),
            "response_style": _listify(mode_data.get("response_style")),
            "typical_language": _listify(mode_data.get("typical_language")),
            "accessible_insight": _listify(mode_data.get("accessible_insight")),
        }

    # fallback from legacy list/string format
    flat = _listify(mode_data)
    return {
        "triggers": flat,
        "response_style": [],
        "typical_language": [],
        "accessible_insight": [],
    }


def load_persona_config(config_path: Path) -> Dict[str, Any]:
    """Load persona config JSON with validation."""
    if not config_path.exists():
        raise FileNotFoundError(f"Config JSON not found: {config_path}")

    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"Failed to parse JSON from {config_path}: {exc}") from exc

    if not isinstance(payload, dict):
        raise ValueError(f"Invalid config format in {config_path}: expected JSON object")

    return payload


def generate_system_prompt(config: Dict[str, Any]) -> str:
    """Generate system prompt text from persona identity + framing fields."""
    persona_name = _clean_item(config.get("persona_name")) or "Unknown Persona"
    role_title = _clean_item(config.get("role_title")) or "Unknown Role"
    department = _clean_item(config.get("department_or_affiliation")) or "Unknown Affiliation"
    archetype = _clean_item(config.get("archetype")) or "Unspecified Archetype"
    summary = _clean_item(config.get("short_identity_summary"))
    relationship = _clean_item(config.get("lived_relationship_to_place"))

    worldview_bits = _listify(config.get("ethical_tensions"))[:4]
    core_tensions = _listify(config.get("core_tensions"))[:6]

    lines: List[str] = []
    lines.append("You are roleplaying a stakeholder in the Harbortown interview simulator.")
    lines.append("")
    lines.append("Identity")
    lines.append(f"- Name: {persona_name}")
    lines.append(f"- Role: {role_title}")
    lines.append(f"- Institutional location: {department}")
    lines.append(f"- Persona archetype: {archetype}")
    if summary:
        lines.append(f"- Identity summary: {summary}")
    if relationship:
        lines.append(f"- Relationship to Harbortown: {relationship}")

    lines.append("")
    lines.append("Worldview and tensions")
    lines.append(_format_bullets(worldview_bits if worldview_bits else ["No explicit worldview notes provided."]))

    lines.append("")
    lines.append("Core tensions to preserve")
    lines.append(_format_bullets(core_tensions if core_tensions else ["No core tensions provided."]))

    lines.append("")
    lines.append("Behavioral framing")
    lines.append("- Stay consistent with the persona’s institutional role and constraints.")
    lines.append("- Use world-bible facts only when grounded in retrieved context.")
    lines.append("- Do not invent private facts, policy commitments, or hidden decisions.")
    lines.append("- If context is missing, answer cautiously and transparently.")

    return "\n".join(lines).strip() + "\n"


def generate_behavior_prompt(config: Dict[str, Any]) -> str:
    """Generate behavior prompt text for tone, disclosure, and consistency rules."""
    tone_rules = _listify(config.get("tone_rules"))
    speech_patterns = _listify(config.get("speech_patterns"))
    vocab_preferences = _listify(config.get("vocabulary_preferences"))
    avoid_terms = _listify(config.get("avoid_terms"))
    guardrails = _listify(config.get("guardrails"))

    behavioral_rules = config.get("behavioral_rules") if isinstance(config.get("behavioral_rules"), dict) else {}
    good_interview = _listify(behavioral_rules.get("react_to_respectful_context_aware_questions"))
    poor_interview = _listify(behavioral_rules.get("react_to_confrontational_questions"))
    increases_openness = _listify(behavioral_rules.get("what_increases_openness"))
    retreat_triggers = _listify(behavioral_rules.get("retreat_triggers_procedural_language"))
    will_not_say = _listify(behavioral_rules.get("will_not_explicitly_say"))

    disclosure_modes = config.get("disclosure_modes") if isinstance(config.get("disclosure_modes"), dict) else {}

    lines: List[str] = []
    lines.append("Apply the following behavior rules exactly when generating responses.")
    lines.append("")

    lines.append("Tone rules")
    lines.append(_format_bullets(tone_rules))
    lines.append("")

    lines.append("Pacing and speech rules")
    lines.append(_format_bullets(speech_patterns))
    lines.append("")

    lines.append("Vocabulary preferences")
    lines.append(_format_bullets(vocab_preferences))
    lines.append("")

    lines.append("Avoid terms / avoid phrasing")
    lines.append(_format_bullets(avoid_terms if avoid_terms else ["No explicit avoid terms provided."]))
    lines.append("")

    lines.append("Disclosure logic by mode")
    for mode in ["baseline", "elevated", "reflective", "defensive", "hypothetical"]:
        mode_sections = _get_mode_sections(disclosure_modes, mode)
        lines.append(f"- Mode: {mode}")
        lines.append(f"  - Triggers: {', '.join(mode_sections['triggers']) if mode_sections['triggers'] else '(none provided)'}")
        lines.append(
            f"  - Response style: {', '.join(mode_sections['response_style']) if mode_sections['response_style'] else '(none provided)'}"
        )
        lines.append(
            f"  - Typical language: {', '.join(mode_sections['typical_language']) if mode_sections['typical_language'] else '(none provided)'}"
        )
        lines.append(
            f"  - Accessible insight: {', '.join(mode_sections['accessible_insight']) if mode_sections['accessible_insight'] else '(none provided)'}"
        )
    lines.append("")

    lines.append("Behavioral reactions")
    lines.append("- How to respond to good interviewing")
    lines.append(_format_bullets(good_interview if good_interview else ["No explicit rules provided."]))
    lines.append("- How to respond to poor interviewing")
    lines.append(_format_bullets(poor_interview if poor_interview else ["No explicit rules provided."]))
    lines.append("- What increases openness")
    lines.append(_format_bullets(increases_openness if increases_openness else ["No explicit rules provided."]))
    lines.append("- What triggers retreat into procedural language")
    lines.append(_format_bullets(retreat_triggers if retreat_triggers else ["No explicit rules provided."]))
    lines.append("- What the persona will not explicitly say")
    lines.append(_format_bullets(will_not_say if will_not_say else ["No explicit constraints provided."]))
    lines.append("")

    lines.append("Hard guardrails")
    lines.append(_format_bullets(guardrails if guardrails else ["No explicit guardrails provided."]))
    lines.append("")

    lines.append("Consistency constraints")
    lines.append("- Stay in persona at all times.")
    lines.append("- Do not contradict role, disclosure logic, or guardrails.")
    lines.append("- Do not reveal hidden certainty if config indicates uncertainty.")
    lines.append("- Keep answers interview-appropriate: clear, bounded, and context-aware.")

    return "\n".join(lines).strip() + "\n"


def generate_runtime_template(config: Dict[str, Any]) -> str:
    """Generate runtime template with placeholders for retrieval + conversation state."""
    persona_name = _clean_item(config.get("persona_name")) or "persona"

    lines: List[str] = []
    lines.append("# Runtime Instruction Template")
    lines.append(f"# Persona: {persona_name}")
    lines.append("")
    lines.append("## 1) Retrieved world knowledge (RAG)")
    lines.append("{{RETRIEVED_WORLD_KNOWLEDGE}}")
    lines.append("")
    lines.append("## 2) Retrieved persona knowledge (optional)")
    lines.append("{{RETRIEVED_PERSONA_KNOWLEDGE}}")
    lines.append("")
    lines.append("## 3) Conversation history")
    lines.append("{{CONVERSATION_HISTORY}}")
    lines.append("")
    lines.append("## 4) Current user question")
    lines.append("{{CURRENT_USER_QUESTION}}")
    lines.append("")
    lines.append("## 5) Response requirements")
    lines.append("- Remain fully consistent with system and behavior prompts.")
    lines.append("- Ground factual statements in retrieved world knowledge when available.")
    lines.append("- Apply persona disclosure logic before revealing deeper insights.")
    lines.append("- Keep answer concise, clear, and interview-appropriate.")
    lines.append("- If evidence is missing, state uncertainty instead of inventing facts.")

    return "\n".join(lines).strip() + "\n"


def save_prompt_files(output_dir: Path, system_prompt: str, behavior_prompt: str, runtime_template: str) -> None:
    """Write prompt files to output directory."""
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "system_prompt.txt").write_text(system_prompt, encoding="utf-8")
    (output_dir / "behavior_prompt.txt").write_text(behavior_prompt, encoding="utf-8")
    (output_dir / "runtime_template.txt").write_text(runtime_template, encoding="utf-8")


def _derive_output_dir(config_path: Path, output_root: Path, config: Dict[str, Any]) -> Path:
    persona_name = _clean_item(config.get("persona_name"))
    if persona_name:
        persona_slug = _slugify(persona_name)
    else:
        persona_slug = _slugify(config_path.stem.replace("_persona_config", ""))
    return output_root / persona_slug


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate persona prompt templates from persona config JSON")
    parser.add_argument("--config-json", type=Path, required=True, help="Path to persona config JSON")
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("backend/persona_prompts"),
        help="Root output folder for generated prompt files",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    try:
        config = load_persona_config(args.config_json)

        system_prompt = generate_system_prompt(config)
        behavior_prompt = generate_behavior_prompt(config)
        runtime_template = generate_runtime_template(config)

        out_dir = _derive_output_dir(args.config_json, args.output_root, config)
        save_prompt_files(out_dir, system_prompt, behavior_prompt, runtime_template)

    except Exception as exc:
        raise SystemExit(f"Failed to build persona prompts: {exc}") from exc

    print("Persona prompt templates generated successfully:")
    print(f"- Config: {args.config_json}")
    print(f"- Output folder: {out_dir}")
    print("- Files:")
    print(f"  - {out_dir / 'system_prompt.txt'}")
    print(f"  - {out_dir / 'behavior_prompt.txt'}")
    print(f"  - {out_dir / 'runtime_template.txt'}")

    print("\nExample run:")
    print(
        "python backend/build_persona_prompt.py --config-json backend/persona_configs/planner_persona_config.json "
        "--output-root backend/persona_prompts"
    )


if __name__ == "__main__":
    main()
