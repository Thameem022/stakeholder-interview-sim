"""Path shim over the repo-root golden dataset loader.

The loader lives at <repo>/lib/golden.py but eval scripts run from backend/, so
`lib` is not importable. Importing it through here keeps the sys.path handling
in exactly one place instead of at the top of every script.

    from evals.lib.golden import load_golden, load_turns, load_jsonl
"""

from __future__ import annotations

import sys
from pathlib import Path

# evals/lib/golden.py -> evals/lib -> evals -> backend -> <repo root>
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lib.golden import (  # noqa: E402,F401
    DIMENSIONS,
    GoldenDatasetError,
    load_golden,
    load_jsonl,
    load_turns,
)

__all__ = ["DIMENSIONS", "GoldenDatasetError", "load_golden", "load_jsonl", "load_turns", "REPO_ROOT"]
