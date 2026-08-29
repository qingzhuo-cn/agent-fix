#!/usr/bin/env python3
"""agent-fix MCP server entry point — a thin launcher.

The implementation lives in agentfix/mcp.py (tool registry + JSON-RPC stdio
loop). This file exists so MCP clients can be registered with a plain
`python <path>/mcp/server.py` command.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentfix.mcp import main

if __name__ == "__main__":
    sys.exit(main())
