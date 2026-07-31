# install.ps1 — install the agent-fix skill + CLI into every detected agent (Windows).
# Run in PowerShell:  powershell -ExecutionPolicy Bypass -File install\install.ps1
# Idempotent: safe to re-run after `git pull` to update.
$ErrorActionPreference = "Stop"

$Repo   = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Name   = "agent-fix"
$HomeD  = $env:USERPROFILE
$Local  = $env:LOCALAPPDATA

function Install-Skill([string]$Target) {
    New-Item -ItemType Directory -Force -Path $Target | Out-Null
    Copy-Item -Recurse -Force "$Repo\SKILL.md" $Target
    Copy-Item -Recurse -Force "$Repo\fixes"    $Target
    Copy-Item -Recurse -Force "$Repo\catalog.json" $Target
    Copy-Item -Recurse -Force "$Repo\scripts"  $Target
    Write-Host "[agent-fix] skill installed -> $Target" -ForegroundColor Green
}

function Install-AgentsMdHook([string]$File) {
    New-Item -ItemType Directory -Force -Path (Split-Path $File) | Out-Null
    $Marker = "# --- /agent-fix ---"
    $exists = Test-Path $File
    if ($exists -and (Select-String -Path $File -Pattern $Marker -Quiet)) {
        Write-Host "[agent-fix] hook already present in $File" -ForegroundColor Yellow
        return
    }
    $hook = @"
`n# --- agent-fix (installed $(Get-Date -Format yyyy-MM-dd)) ---
When asked to fix a broken AI coding agent, use the agent-fix skill at $Repo
  - Read $Repo\fixes\*.md (knowledge base) and $Repo\SKILL.md
  - Run: python "$Repo\scripts\fix.py" doctor   (then: fix apply <id> --yes)
# --- /agent-fix ---
"@
    Add-Content -Path $File -Value $hook
    Write-Host "[agent-fix] AGENTS.md hook appended -> $File" -ForegroundColor Green
}

function Install-Cli() {
    $binDir = Join-Path $HomeD "bin"
    if (-not (Test-Path $binDir)) { New-Item -ItemType Directory -Force -Path $binDir | Out-Null }
    $cmd = Join-Path $binDir "fix.cmd"
    # exec shim that points at the real repo script (a plain copy would break:
    # the wrapper resolves its own location and would look for bin\fix.py)
    @"
@echo off
python "$Repo\scripts\fix.py" %*
"@ | Set-Content -Path $cmd -Encoding ASCII
    Write-Host "[agent-fix] CLI installed -> $cmd (add $binDir to PATH if needed)" -ForegroundColor Green
}

Write-Host "[agent-fix] installing from $Repo" -ForegroundColor Cyan

# --- skill targets ---
if (Test-Path $Local) {
    Install-Skill (Join-Path $Local "hermes\skills\$Name")
} else {
    Install-Skill (Join-Path $HomeD ".local\share\hermes\skills\$Name")
}

if (Test-Path (Join-Path $HomeD ".claude")) {
    Install-Skill (Join-Path $HomeD ".claude\skills\$Name")
} else {
    Write-Host "[agent-fix] Claude Code not detected (.claude missing), skipped" -ForegroundColor Yellow
}

if (Test-Path (Join-Path $HomeD ".config\opencode")) {
    Install-Skill (Join-Path $HomeD ".config\opencode\skill\$Name")
} else {
    Write-Host "[agent-fix] OpenCode not detected (.config\opencode missing), skipped" -ForegroundColor Yellow
}

if (Test-Path (Join-Path $HomeD ".codex")) {
    Install-Skill (Join-Path $HomeD ".codex\skills\$Name")
} else {
    Write-Host "[agent-fix] Codex not detected (.codex missing), skipped" -ForegroundColor Yellow
}

if (Test-Path (Join-Path $HomeD ".kimi-code")) {
    Install-Skill (Join-Path $HomeD ".kimi-code\skills\$Name")
} else {
    Write-Host "[agent-fix] Kimi Code not detected (.kimi-code missing), skipped" -ForegroundColor Yellow
}

if (Test-Path (Join-Path $HomeD ".pi")) {
    Install-Skill (Join-Path $HomeD ".pi\agent\skills\$Name")
} else {
    Write-Host "[agent-fix] Pi not detected (.pi missing), skipped" -ForegroundColor Yellow
}

# shared skills dir used by ZCode and others — always install
Install-Skill (Join-Path $HomeD ".agents\skills\$Name")

# --- AGENTS.md hooks (Codex, OpenCode) ---
Install-AgentsMdHook (Join-Path $HomeD ".codex\AGENTS.md")
Install-AgentsMdHook (Join-Path $HomeD ".config\opencode\AGENTS.md")

# --- MCP server registration ---
Write-Host "[agent-fix] registering MCP server with detected agents..." -ForegroundColor Cyan
try {
    & python "$Repo\scripts\mcp_register.py" all
} catch {
    Write-Host "[agent-fix] MCP registration incomplete (see mcp\README.md)" -ForegroundColor Yellow
}

# --- Self-heal startup hooks (claude/codex/opencode/hermes) ---
Write-Host "[agent-fix] registering self-heal startup hooks..." -ForegroundColor Cyan
try {
    & python "$Repo\scripts\heal_hooks.py" install
} catch {
    Write-Host "[agent-fix] self-heal hooks incomplete (run scripts\heal_hooks.py install later)" -ForegroundColor Yellow
}

# --- CLI ---
Install-Cli

Write-Host "[agent-fix] done. Try: fix doctor" -ForegroundColor Cyan
