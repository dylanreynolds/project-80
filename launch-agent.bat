@echo off
setlocal EnableDelayedExpansion
title Project 80 — Autobot Agent (Windows 11 VM)

echo.
echo  ============================================================
echo   Project 80 — Autobot Local Agent
echo   Windows 11 VM ^| Polls orchestrator for install jobs
echo  ============================================================
echo.

REM ── Load secrets ────────────────────────────────────────────────
if not exist .env.local (
    echo [ERROR] .env.local not found.
    echo         Copy .env.local from your team lead and place it in this folder.
    pause
    exit /b 1
)
for /f "usebackq tokens=1,2 delims==" %%a in (".env.local") do (
    if not "%%a"=="" if not "%%a:~0,1%"=="#" set "%%a=%%b"
)
echo [OK] Loaded .env.local

REM ── Apply Gilligan's Island env vars ───────────────────────────
set USE_HTTP_POLLING=true

REM Use loaded values with fallbacks
if "%ORCHESTRATOR_URL%"=="" set ORCHESTRATOR_URL=http://192.168.56.10:8000
if "%ORCHESTRATOR_API_KEY%"=="" (
    echo [ERROR] ORCHESTRATOR_API_KEY not set in .env.local
    pause
    exit /b 1
)
if "%COMMAND_SIGNING_SECRET%"=="" (
    echo [ERROR] COMMAND_SIGNING_SECRET not set in .env.local
    pause
    exit /b 1
)
if "%AGENT_USER_EMAIL%"=="" set AGENT_USER_EMAIL=demo.user@subwayfranchise.com

echo.
echo   Settings:
echo     ORCHESTRATOR_URL:    %ORCHESTRATOR_URL%
echo     AGENT_USER_EMAIL:    %AGENT_USER_EMAIL%
echo     USE_HTTP_POLLING:    true
echo.

REM ── Python check ────────────────────────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Install Python 3.10+ from https://python.org
    pause
    exit /b 1
)

REM ── Install deps if venv missing ────────────────────────────────
if not exist local-agent\.venv (
    echo Creating Python virtual environment for agent...
    python -m venv local-agent\.venv
    echo Installing dependencies...
    local-agent\.venv\Scripts\pip install -q -r local-agent\requirements.txt
    echo [OK] Agent dependencies installed
) else (
    echo [OK] Agent virtual environment ready
)

REM ── Connectivity check ──────────────────────────────────────────
echo.
echo Checking orchestrator at %ORCHESTRATOR_URL%...
curl -sf "%ORCHESTRATOR_URL%/health" >nul 2>&1
if errorlevel 1 (
    echo [WARN] Cannot reach orchestrator — make sure start-debian.sh is running on the Debian VM.
    echo        The agent will keep retrying once started.
) else (
    echo [OK] Orchestrator is up
)

REM ── Log dir ─────────────────────────────────────────────────────
if not exist "C:\ProgramData\ITAgent" mkdir "C:\ProgramData\ITAgent"

REM ── Show what PowerShell can do ─────────────────────────────────
echo.
echo   Demo PowerShell capabilities:
echo     winget install --id ^<package^>   — silent software install
echo     Get-AppxPackage                   — list installed apps
echo     Get-Process ^| Sort CPU -Desc     — top processes by CPU
echo     Test-NetConnection ^<host^>        — network connectivity test
echo     Get-WindowsUpdateLog              — Windows Update history
echo.

REM ── Start agent ─────────────────────────────────────────────────
echo Starting Autobot agent... ^(Ctrl+C to stop^)
echo.
local-agent\.venv\Scripts\python local-agent\agent.py
