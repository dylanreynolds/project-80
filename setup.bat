@echo off
setlocal EnableDelayedExpansion
title Project 80 — Setup

echo.
echo  ============================================================
echo   Project 80 — IT Automation Hackathon Setup
echo   Subway Franchise World Headquarters
echo  ============================================================
echo.

REM ── Check Python ────────────────────────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python 3 is not installed or not in PATH.
    echo         Install from https://python.org ^(tick "Add to PATH"^)
    pause
    exit /b 1
)
for /f "tokens=2 delims= " %%v in ('python --version') do set PY_VER=%%v
echo [OK] Python %PY_VER%

REM ── Check pip ───────────────────────────────────────────────────
pip --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] pip not found. Run: python -m ensurepip
    pause
    exit /b 1
)
echo [OK] pip found

REM ── Check git ───────────────────────────────────────────────────
git --version >nul 2>&1
if errorlevel 1 (
    echo [WARN] git not found — skipping repo check
) else (
    echo [OK] git found
)

echo.
echo  ── Step 1: Install Python dependencies ──────────────────────
echo.

echo Installing orchestrator dependencies...
pip install -q -r orchestrator\requirements.txt
if errorlevel 1 ( echo [ERROR] orchestrator deps failed & pause & exit /b 1 )
echo [OK] Orchestrator deps installed

echo Installing teams-bot dependencies...
pip install -q -r teams-bot\requirements.txt
if errorlevel 1 ( echo [ERROR] teams-bot deps failed & pause & exit /b 1 )
echo [OK] Teams-bot deps installed

echo Installing local-agent dependencies...
pip install -q -r local-agent\requirements.txt
if errorlevel 1 ( echo [ERROR] local-agent deps failed & pause & exit /b 1 )
echo [OK] Local-agent deps installed

echo.
echo  ── Step 2: Generate shared secrets ──────────────────────────
echo.

REM Generate signing secret if not already in .env.local
set ENV_FILE=.env.local
if exist %ENV_FILE% (
    echo [OK] %ENV_FILE% already exists — skipping secret generation
    echo      Delete %ENV_FILE% to regenerate secrets.
    goto :secrets_done
)

echo Generating COMMAND_SIGNING_SECRET...
for /f %%s in ('python -c "import secrets; print(secrets.token_hex(32))"') do set SIGNING_SECRET=%%s

echo Generating ORCHESTRATOR_API_KEY...
for /f %%s in ('python -c "import secrets; print(secrets.token_urlsafe(24))"') do set API_KEY=%%s

echo Writing %ENV_FILE%...
(
echo # Project 80 — Generated secrets
echo # Share this file with your team ^(keep out of git — it's in .gitignore^)
echo #
echo COMMAND_SIGNING_SECRET=%SIGNING_SECRET%
echo ORCHESTRATOR_API_KEY=%API_KEY%
echo.
echo # Network — update these to match your VirtualBox host-only network
echo # Host laptop is usually 192.168.56.1, Debian VM is 192.168.56.10
echo GILLIGAN_URL=http://192.168.56.1:3000
echo ORCHESTRATOR_URL=http://192.168.56.10:8000
echo GILLIGAN_HOST_IP=192.168.56.1
echo DEBIAN_VM_IP=192.168.56.10
echo WINDOWS_VM_IP=192.168.56.20
) > %ENV_FILE%

echo [OK] Secrets written to %ENV_FILE%
echo.
echo  !! ACTION REQUIRED: Share %ENV_FILE% with your team members.
echo     They must put it in the project root before running launch.bat
echo.

:secrets_done

REM ── Step 3: Check Node / Gilligan's Island ──────────────────────
echo  ── Step 3: Check Gilligan's Island (project-gilligan) ───────
echo.
node --version >nul 2>&1
if errorlevel 1 (
    echo [WARN] Node.js not found — Gilligan's Island won't start from this machine.
    echo        Install Node.js from https://nodejs.org or run Gilligan's Island manually.
) else (
    for /f %%v in ('node --version') do echo [OK] Node.js %%v
    if exist "..\hackathon-offboarding-mcp\package.json" (
        echo [OK] Gilligan's Island found at ..\hackathon-offboarding-mcp
    ) else (
        echo [WARN] Gilligan's Island not found at ..\hackathon-offboarding-mcp
        echo        Update GILLIGAN_URL in %ENV_FILE% to point at wherever it's running.
    )
)

REM ── Step 4: Check ngrok ─────────────────────────────────────────
echo.
echo  ── Step 4: Check ngrok ──────────────────────────────────────
echo.
ngrok --version >nul 2>&1
if errorlevel 1 (
    echo [WARN] ngrok not found in PATH.
    echo        Download from https://ngrok.com and add to PATH, or install:
    echo          winget install ngrok.ngrok
) else (
    for /f "tokens=3" %%v in ('ngrok --version') do echo [OK] ngrok %%v
)

REM ── Summary ─────────────────────────────────────────────────────
echo.
echo  ============================================================
echo   Setup complete!
echo  ============================================================
echo.
echo   NEXT STEPS:
echo.
echo   On HOST LAPTOP:
echo     1. Start Gilligan's Island:   launch-host.bat
echo     2. In a new terminal:         ngrok http 3978
echo        Copy the https URL to your Azure Bot messaging endpoint
echo.
echo   On DEBIAN VM ^(192.168.56.10^):
echo     3. Copy .env.local to the VM
echo     4. Run: bash gilligan/start-debian.sh
echo.
echo   On WINDOWS 11 VM ^(192.168.56.20^):
echo     5. Copy .env.local to the VM
echo     6. Run: launch-agent.bat
echo.
echo   DEMO — after a Teams user requests software:
echo     7. Run: python gilligan\demo\approve.py
echo.
pause
