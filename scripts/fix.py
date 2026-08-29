#!/usr/bin/env python3
"""agent-fix CLI entry point — a thin launcher.

The implementation lives in the agentfix/ package next to this file, so the
same entry works from a repo checkout AND from a deployed skill copy
(SKILL.md + fixes/ + catalog.json + scripts/ + agentfix/ are copied together).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentfix.cli import main

if __name__ == "__main__":
    sys.exit(main())
