#requires -Version 5.1
<#
  setup.ps1 — das-dev PC init: Hackaboom (mirror) + Nightshift (session lifeline)

  Run from DasSetup.bat or:  powershell -NoProfile -ExecutionPolicy Bypass -File setup.ps1
  Notes:
    - Server lives at $ServerUrl (das-server over Tailscale). Health-checked at the end.
    - Idempotent: safe to re-run; re-run = update (git pull + rebuild).
#>
param(
  [string]$ServerUrl = "http://100.104.181.43:8871",
  [string]$BaseDir = "$env:USERPROFILE\das",
  [switch]$AutoInstall,
  [switch]$NoTunnel
)

$ErrorActionPreference = "Stop"
$ok = 0; $warn = 0
function Step($msg)   { Write-Host "==> $msg" -ForegroundColor Gray }
function Good($msg)   { Write-Host "    ok  $msg" -ForegroundColor Green; $script:ok++ }
function Warn($msg)   { Write-Host "  [!] $msg" -ForegroundColor Yellow; $script:warn++ }
function Die($msg)    { Write-Host "  [x] $msg" -ForegroundColor Red; exit 1 }

function Ensure($cmd, $pkg, $check) {
  if (Get-Command $cmd -ErrorAction SilentlyContinue) { Good "$cmd present"; return $true }
  if ($AutoInstall) {
    Step "winget install $pkg (missing)"
    winget install -e --id $pkg --accept-source-agreements --accept-package-agreements | Out-Null
    if (Get-Command $cmd -ErrorAction SilentlyContinue) { Good "$cmd installed"; return $true }
  }
  Warn "missing: $cmd ($pkg) — install manually, skipping section"
  return $false
}

Write-Host "== das-dev setup: hackaboom + nightshift ==" -ForegroundColor Cyan

# --- 0. tool checks -----------------------------------------------------------
$git      = Ensure "git"   "Git.Git"
$code     = Ensure "code"  "Microsoft.VisualStudioCode"
$uv       = Ensure "uv"    "astral-sh.uv"
$py       = Ensure "py"    "Python.Python.3.12"
$ts       = Ensure "tailscale" "tailscale.tailscale"
$claude   = Get-Command "claude" -ErrorAction SilentlyContinue
$cursor   = Get-Command "cursor-agent" -ErrorAction SilentlyContinue
if (-not $claude) { Warn "claude CLI not on PATH — the mirror hook needs Claude Cod installed" }
if (-not $cursor) { Warn "cursor-agent CLI not on PATH — cursor mirroring needs it" }

# --- 1. repos -----------------------------------------------------------------
Step "repos under $BaseDir"
New-Item -ItemType Directory -Force -Path "$BaseDir\repos" | Out-Null
foreach ($r in @("hackaboom","nightshift")) {
  if (Test-Path "$BaseDir\repos\$r\.git") {
    git -C "$BaseDir\repos\$r" pull --ff-only
    Good "$r updated"
  } elseif ($git) {
    git clone "https://github.com/DasVR/$r.git" "$BaseDir\repos\$r"
    Good "$r cloned"
  } else { Warn "git missing — could not fetch $r" }
}

# --- 2. hackaboom PC mirror (VS Code extension) --------------------------------
if ($code) {
  Step "hackaboom extension"
  Push-Location "$BaseDir\repos\hackaboom\extension"
  npm ci 2>$null; if ($LASTEXITCODE -ne 0) { npm install --no-audit --no-fund }
  if (Test-Path "$BaseDir\repos\hackaboom\extension\package.json") {
    npx --yes @vscode/vsce package --no-dependencies -o dist 2>$null
    $vsix = Get-ChildItem "$BaseDir\repos\hackaboom\extension\dist\*.vsix" | Select-Object -First 1
    if ($vsix) { code --install-extension $vsix.FullName --force; Good "extension installed: $($vsix.Name)" }
    else { Warn "vsix build failed — see extension/README for the manual build" }
  }
  # owner credentials: opt-in telemetry stays OFF; mirror on the local bridge :8788
  $creds = "$env:USERPROFILE\.hackaboom.json"
  if (-not (Test-Path $creds)) {
    '{"hackaboom": {"mirror": {"enabled": true, "host": "127.0.0.1", "port": 8788}, "telemetry": {"enabled": false}}}' | Set-Content -Encoding UTF8 $creds
    Good "wrote $creds (telemetry off)"
  } else { Good "credentials already exist"
  }
  Pop-Location
}

# --- 3. nightshift collector (PC side) ------------------------------------------
if ($uv) {
  Step "nightshift collector"
  New-Item -ItemType Directory -Force -Path "$BaseDir\venvs" | Out-Null
  uv venv "$BaseDir\venvs\nightshift" 2>$null
  uv pip install --python "$BaseDir\venvs\nightshift\Scripts\python.exe" -e "$BaseDir\repos\nightshift" 2>$null
  if ($LASTEXITCODE -eq 0) { Good "collector package installed (venv $BaseDir\venvs\nightshift)" }
  else { Warn "pip install -e nightshift failed — run manually: uv pip install -e $BaseDir\repos\nightshift" }
  [Environment]::SetEnvironmentVariable("NIGHTSHIFT_SERVER_URL", $ServerUrl, "User")
  Good "NIGHTSHIFT_SERVER_URL = $ServerUrl (User env)"
}

# --- 4. command shims ------------------------------------------------------------
Step "shims in $BaseDir\bin"
New-Item -ItemType Directory -Force -Path "$BaseDir\bin" | Out-Null
$pyBin = "$BaseDir\venvs\nightshift\Scripts\python.exe"
if (Test-Path $pyBin) {
  "py `"%~dp0..\\venvs\\nightshift\\Scripts\\python.exe`" -m collector %*" | Out-Null
  "@echo off`r`n`"%pyBin%`" -m collector %*" | Set-Content "$BaseDir\bin\shift.bat" -Encoding Ascii
  Good "shift.bat (collect + push: shift --since 18:00 --push)"
}
Write-Host "    add to PATH if not already:  $BaseDir\bin"

# --- 5. tunneled panel view (hackaboom server editor) -----------------------------
if (-not $NoTunnel) {
  Step "panel tunnel helper"
  "@echo off`r`necho resume desk http://127.0.0.1:8791  editor panel http://127.0.0.1:8788`r`nssh -N -L 8791:127.0.0.1:8791 -L 8788:127.0.0.1:8788 das@100.104.181.43" |
    Set-Content "$BaseDir\bin\panel-tunnel.bat" -Encoding Ascii
  Good "panel-tunnel.bat (resume desk :8791 and editor panel :8788, over ssh)"
}

# --- 6. connectivity ---------------------------------------------------------------
Step "server health $ServerUrl"
try {
  $h = Invoke-RestMethod "$ServerUrl/health" -TimeoutSec 8
  if ($h.status -eq "ok") { Good "nightshift server reachable ($($h.version))" }
} catch { Warn "server unreachable at $ServerUrl — check Tailscale is up (tailscale status)" }
if ($ts) { tailscale status | Select-Object -First 1 }

# --- summary -------------------------------------------------------------------------
Write-Host ""
Write-Host "done: $ok ok, $warn warnings" -ForegroundColor Cyan
Write-Host "nightly flow:"
Write-Host "  1. code like normal (claude + cursor mirror into local VS Code)"
Write-Host "  2. shift --since 18:00 --push          # ship today's sessions to das-server"
Write-Host "  3. panel-tunnel.bat                    # watch the server editor typing live"
Write-Host "  4. discord/iMessage ping when the run finishes"