"""agent-fix — universal diagnostic & repair toolkit for AI coding agents.

Package layout (each concept lives in exactly one module):
    catalog  catalog.json access: agents, issues, per-platform paths, templates
    report   output hygiene: secret masking, quoting, [DATA] tags
    engine   checks / fixes / self-heal + network, audit, backup, logs, provider
    hooks    startup hooks, MCP registration, `fix install` / `fix uninstall`
    cli      the `fix` command-line interface
    mcp      zero-dependency MCP stdio server exposing the engine as tools
"""

__version__ = "2.0.0"
