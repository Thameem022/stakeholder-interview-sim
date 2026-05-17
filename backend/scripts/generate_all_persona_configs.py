#!/usr/bin/env python3
"""
Generate raw + normalized persona configs for all .docx files in backend/Persona_dossier.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


def slugify_name(filename_stem: str) -> str:
    # Example: "Persona 2_Waterfront Resident Dossier" -> "waterfront_resident"
    name = re.sub(r"(?i)^persona\s*\d+\s*[_-]\s*", "", filename_stem).strip()
    name = re.sub(r"(?i)\s*dossier$", "", name).strip()
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", name).strip("_").lower()
    return slug or "persona"


def main() -> int:
    repo_root = Path(__file__).resolve().parent
    dossier_dir = repo_root / "Persona_dossier"
    out_dir = repo_root / "persona_configs"
    script_path = repo_root / "build_persona_config.py"

    if not dossier_dir.exists():
        print(f"Missing folder: {dossier_dir}")
        return 1
    if not script_path.exists():
        print(f"Missing script: {script_path}")
        return 1

    out_dir.mkdir(parents=True, exist_ok=True)
    docs = sorted(dossier_dir.glob("*.docx"))

    if not docs:
        print(f"No .docx files found in {dossier_dir}")
        return 1

    print(f"Found {len(docs)} dossier(s).")
    failures = []

    for doc in docs:
        slug = slugify_name(doc.stem)
        raw_out = out_dir / f"{slug}_persona_config.raw.json"
        norm_out = out_dir / f"{slug}_persona_config.json"

        cmd = [
            sys.executable,
            str(script_path),
            "--input-docx",
            str(doc),
            "--output-json",
            str(raw_out),
            "--normalized-output",
            str(norm_out),
        ]

        print(f"\nProcessing: {doc.name}")
        result = subprocess.run(cmd, text=True, capture_output=True)

        if result.returncode == 0:
            print(f"  raw: {raw_out.name}")
            print(f"   norm: {norm_out.name}")
        else:
            print(f"   failed ({doc.name})")
            if result.stderr.strip():
                print(result.stderr.strip())
            failures.append(doc.name)

    print("\nDone.")
    if failures:
        print("Failed dossiers:")
        for f in failures:
            print(f" - {f}")
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())