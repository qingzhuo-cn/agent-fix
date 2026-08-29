# install.ps1 — thin bootstrap: let the agent-fix CLI deploy itself.
#
# Everything (skill copies, AGENTS.md hooks, startup hooks, MCP registration,
# the `fix` CLI shim) is implemented once in agentfix/hooks.py and driven by
# catalog.json. This script only locates python. `fix uninstall` reverses it.
# Idempotent: safe to re-run after `git pull`.

$Repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { $py = Get-Command py -ErrorAction SilentlyContinue }
if (-not $py) {
    Write-Error "no python found (looked for python/py)"
    exit 2
}

& $py.Source (Join-Path $Repo "scripts\fix.py") install
exit $LASTEXITCODE
