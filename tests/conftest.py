"""Pytest setup: make the repo root importable.

The root-level launcher `dashboard.py` is a standalone script (not part of the
installed `adaptive_rag` package), so tests that import it need the repo root on
sys.path. pytest's default import mode only adds the tests/ directory.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
