"""
generate_all_persona_prompts.py

Bulk-generate persona prompt templates for all normalized persona config JSON files.

By default it scans:
- backend/persona_configs/*_persona_config.json
and skips:
- *.raw.json

For each config it runs build_persona_prompt.py and writes output into:
- backend/persona_prompts/<persona_name_slug>/

Example:
    /opt/anaconda3/envs/glob/bin/python backend/generate_all_persona_prompts.py
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import List


def discover_configs(config_dir: Path) -> List[Path]:
    """Find normalized persona config files (exclude raw configs)."""
    configs = sorted(config_dir.glob("*_persona_config.json"))
    return [p for p in configs if not p.name.endswith(".raw.json") and ".raw." not in p.name]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate prompt files for all persona config JSON files")
    parser.add_argument(
        "--config-dir",
        type=Path,
        default=Path("backend/persona_configs"),
        help="Folder containing persona config JSON files",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("backend/persona_prompts"),
        help="Root folder where persona prompt folders will be created",
    )
    parser.add_argument(
        "--prompt-script",
        type=Path,
        default=Path("backend/build_persona_prompt.py"),
        help="Path to single-persona prompt generation script",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    config_dir = args.config_dir
    output_root = args.output_root
    prompt_script = args.prompt_script

    if not config_dir.exists():
        print(f"Config directory not found: {config_dir}")
        return 1
    if not prompt_script.exists():
        print(f"Prompt builder script not found: {prompt_script}")
        return 1

    configs = discover_configs(config_dir)
    if not configs:
        print(f"No normalized persona configs found in: {config_dir}")
        return 1

    output_root.mkdir(parents=True, exist_ok=True)

    print(f"Found {len(configs)} persona config(s).")
    failures: List[str] = []

    for config_path in configs:
        cmd = [
            sys.executable,
            str(prompt_script),
            "--config-json",
            str(config_path),
            "--output-root",
            str(output_root),
        ]

        print(f"\nGenerating prompts for: {config_path.name}")
        result = subprocess.run(cmd, text=True, capture_output=True)

        if result.returncode == 0:
            print("  success")
        else:
            print("  failed")
            if result.stderr.strip():
                print(result.stderr.strip())
            failures.append(config_path.name)

    print("\nBulk prompt generation complete.")

    if failures:
        print("Failed files:")
        for name in failures:
            print(f" - {name}")
        return 2

    print(f"All prompts generated under: {output_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
