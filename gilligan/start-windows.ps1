# =============================================================================
# Gilligan's Island — Windows 11 VM startup script (Autobot local agent)
# Run from PowerShell as a regular user (not Administrator, unless winget needs it).
# =============================================================================

$ErrorActionPreference = "Stop"

$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDir = Split-Path -Parent $ScriptDir
$AgentDir   = Join-Path $ProjectDir "local-agent"

Write-Host "=== Gilligan's Island — Windows 11 VM (Autobot) ===" -ForegroundColor Cyan
Write-Host "Agent directory: $AgentDir"
Write-Host ""

# ── Environment variables ─────────────────────────────────────────────────────
# Set these to match your .env.gilligan (Debian VM values above)
$env:USE_HTTP_POLLING         = "true"
$env:ORCHESTRATOR_URL         = "http://192.168.56.10:8000"   # Debian VM IP
$env:ORCHESTRATOR_API_KEY     = "demo-api-key-change-me"       # must match Debian VM
$env:COMMAND_SIGNING_SECRET   = "REPLACE_WITH_GENERATED_SECRET"  # must match Debian VM
$env:AGENT_USER_EMAIL         = "demo.user@subwayfranchise.com"

# Log dir
$LogDir = "C:\ProgramData\ITAgent"
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }

Write-Host "Env vars set:" -ForegroundColor Yellow
Write-Host "  ORCHESTRATOR_URL:       $($env:ORCHESTRATOR_URL)"
Write-Host "  ORCHESTRATOR_API_KEY:   $($env:ORCHESTRATOR_API_KEY)"
Write-Host "  AGENT_USER_EMAIL:       $($env:AGENT_USER_EMAIL)"
Write-Host "  USE_HTTP_POLLING:       $($env:USE_HTTP_POLLING)"
Write-Host ""

# ── Python virtual environment ────────────────────────────────────────────────
$VenvPath = Join-Path $AgentDir ".venv"
if (-not (Test-Path $VenvPath)) {
    Write-Host "Creating Python virtual environment..." -ForegroundColor Yellow
    python -m venv $VenvPath
}

$PythonExe = Join-Path $VenvPath "Scripts\python.exe"
$PipExe    = Join-Path $VenvPath "Scripts\pip.exe"

Write-Host "Installing agent dependencies..." -ForegroundColor Yellow
& $PipExe install -q --upgrade pip
& $PipExe install -q -r (Join-Path $AgentDir "requirements.txt")

# ── Connectivity check ────────────────────────────────────────────────────────
Write-Host ""
Write-Host "Checking orchestrator connectivity..." -ForegroundColor Yellow
try {
    $resp = Invoke-WebRequest -Uri "$($env:ORCHESTRATOR_URL)/health" -UseBasicParsing -TimeoutSec 5
    $health = $resp.Content | ConvertFrom-Json
    Write-Host "  Orchestrator OK — mode: $($health.mode)" -ForegroundColor Green
} catch {
    Write-Host "  WARNING: Cannot reach orchestrator at $($env:ORCHESTRATOR_URL)" -ForegroundColor Red
    Write-Host "  Make sure the Debian VM is running and start-debian.sh has completed."
    Write-Host "  The agent will keep retrying — continuing anyway."
}

# ── Show demo PowerShell capabilities ────────────────────────────────────────
Write-Host ""
Write-Host "Demo PowerShell capabilities available on this VM:" -ForegroundColor Cyan
Write-Host "  winget search <app>          — find software"
Write-Host "  winget install --id <id>     — install silently"
Write-Host "  Get-AppxPackage              — list installed apps"
Write-Host "  Get-Process | Sort CPU -Desc — top processes"
Write-Host "  Test-NetConnection <host>    — network test"
Write-Host ""

# ── Start the agent ───────────────────────────────────────────────────────────
Write-Host "Starting Autobot local agent..." -ForegroundColor Green
Write-Host "Polling orchestrator every 5 seconds for jobs."
Write-Host "Press Ctrl+C to stop."
Write-Host ""

Set-Location $AgentDir
& $PythonExe agent.py
