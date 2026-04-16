"""Ensure the repo root is on sys.path so `import prototype` works when
running pytest from either the repo root or the prototype directory."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
