"""Foundry entry point: LAB_ROLE selects the module (orchestrator or specialist)."""

import importlib
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

if __name__ == "__main__":
    role = os.environ["LAB_ROLE"]
    if role not in ("orchestrator", "specialist"):
        raise SystemExit("LAB_ROLE must be orchestrator or specialist")
    importlib.import_module(role).main()
