"""Test configuration.

Makes ``scripts/generate_synthetic_data.py`` and the ``src`` package tree
importable by tests without installing the project.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
SRC_DIR = REPO_ROOT / "src"

for path in (SCRIPTS_DIR, SRC_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
