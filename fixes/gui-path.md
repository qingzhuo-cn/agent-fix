# GUI apps can't find agent binaries (shell PATH ≠ GUI PATH)

- **ID:** `gui-path-blind`
- **Affects:** CC-Switch and other desktop launchers/checkers, VS Code terminals,
  any GUI-launched process; all agents (`claude`, `codex`, `opencode`, `hermes`).
- **Tags:** `windows`, `path`, `cc-switch`, `gui`

## Symptom

- A GUI tool (e.g. **CC-Switch**, the Claude/Codex/OpenCode provider switcher) reports
  an agent as **"已安装 · 无法运行" (installed · cannot run)**.
- The same command works perfectly in your terminal (`opencode --version` → v1.18.10).
- The tool's check logic is simple: *find executable → run `--version` → non-zero exit
  = cannot run*. It fails because the GUI process does not inherit your shell PATH.

## Root cause

On Windows, GUI apps launched from Explorer/Start Menu do **not** read your shell's
PATH (bash/zsh `~/.bashrc` exports, MSYS paths, or temporary `export PATH=...`).
They only see the **registry PATH** (`HKCU\Environment` + `HKLM\SYSTEM\...\Environment`).
Binaries that live in:

- `%APPDATA%\npm` (npm global shims: `claude`, `codex`, `opencode` …)
- `%LOCALAPPDATA%\hermes\hermes-agent\venv\Scripts` (`hermes`)
- MSYS2/git-bash `\usr\bin`

are invisible to GUI tools unless explicitly added to the registry PATH.

## Check

```bash
# What a GUI process sees (registry PATH, exactly as Explorer would launch it):
cmd //c "echo %PATH%" | tr ';' '\n' | grep -iE "npm|hermes|claude|codex|opencode" || echo "NOT in GUI PATH"

# What your shell sees:
echo "$PATH" | tr ':' '\n' | grep -iE "npm|hermes" || true

# Reproduce the GUI probe exactly (CreateNoWindow + registry PATH + redirected output):
powershell -NoProfile -Command '
$userPath = (Get-ItemProperty -Path "HKCU:\Environment").Path
$machinePath = (Get-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Environment").Path
$cleanPath = $userPath + ";" + $machinePath
$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = "opencode"
$psi.Arguments = "--version"
$psi.UseShellExecute = $false
$psi.RedirectStandardOutput = $true
$psi.RedirectStandardError = $true
$psi.CreateNoWindow = $true
$psi.Environment["PATH"] = $cleanPath
$p = [System.Diagnostics.Process]::Start($psi)
"stdout=[" + $p.StandardOutput.ReadToEnd() + "] exit=" + $p.ExitCode
'
```

If `cmd //c "echo %PATH%"` lacks the npm/hermes dirs while your shell has them, the
GUI is PATH-blind.

## Fix

Add the missing directories to the **user** PATH (registry), not just the shell:

```bash
# Find the dirs to add
NPM_GLOBAL="$(npm config get prefix)"            # e.g. C:\Users\you\AppData\Roaming\npm
HERMES_BIN="$(dirname "$(command -v hermes)")"   # e.g. C:\Users\you\AppData\Local\hermes\hermes-agent\venv\Scripts

# Append them to the user PATH (idempotent-ish; re-run is safe)
powershell -NoProfile -Command '
$dirs = @(
  "'"$NPM_GLOBAL"'",
  "'"$HERMES_BIN"'"
) | Where-Object { $_ -and -not ([System.IO.Path]::GetFullPath($_) -in ([Environment]::GetEnvironmentVariable("Path","User") -split ";")) }
if ($dirs) {
  $new = ([Environment]::GetEnvironmentVariable("Path","User") + ";" + ($dirs -join ";"))
  [Environment]::SetEnvironmentVariable("Path", $new, "User")
  "Added: " + ($dirs -join "; ")
} else { "All dirs already in user PATH" }
'
```

> `setx PATH` also works but truncates at 1024 chars and can corrupt long PATHs —
> prefer `[Environment]::SetEnvironmentVariable`.

After adding, **restart the GUI app** (it caches PATH at startup). Verify with the
reproduction probe above — it should now exit 0.

## Verify

Re-run the CC-Switch / GUI tool refresh, or the PowerShell probe: `exit=0` and the
version string printed means the GUI can now run the agent.

## Prevention

- After installing any agent, add its bin dir to the **user** PATH via the registry.
- Prefer `[Environment]::SetEnvironmentVariable` over `setx` (1024-char truncation).
- Remember GUI apps read PATH at launch — changes require an app restart.

## Notes

- This is a *diagnosis* issue more than a *broken-install* issue: the agent is fine,
  the environment is blind. Don't reinstall the agent; fix the PATH.
- The same logic applies on macOS/Linux for apps launched via LaunchAgent/desktop
  entries with a minimal PATH — add the dirs in the app's plist/desktop `Environment`.
