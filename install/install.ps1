# install.ps1 — thin bootstrap: locate Python, then delegate one explicit
# install to the agent-fix CLI. Startup self-heal and MCP registration remain opt-in.

$Repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { $py = Get-Command py -ErrorAction SilentlyContinue }
if (-not $py) {
    Write-Error "no python found (looked for python/py)"
    exit 2
}

& $py.Source (Join-Path $Repo "scripts\fix.py") install @args
exit $LASTEXITCODE
