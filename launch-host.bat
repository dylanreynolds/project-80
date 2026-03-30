@echo off
setlocal EnableDelayedExpansion
title Project 80 — Host Launcher (Gilligan's Island + ngrok)

echo.
echo  ============================================================
echo   Project 80 — Host Laptop Launcher
echo   Starts: Gilligan's Island (port 3000) + ngrok (port 3978)
echo  ============================================================
echo.

REM ── Load secrets ────────────────────────────────────────────────
if not exist .env.local (
    echo [ERROR] .env.local not found.
    echo         Run setup.bat first, or get .env.local from your team lead.
    pause
    exit /b 1
)
for /f "usebackq tokens=1,2 delims==" %%a in (".env.local") do (
    if not "%%a"=="" if not "%%a:~0,1%"=="#" set "%%a=%%b"
)
echo [OK] Loaded .env.local

REM ── Verify Gilligan's Island ─────────────────────────────────────
set GILLIGAN_DIR=..\hackathon-offboarding-mcp
if not exist "%GILLIGAN_DIR%\package.json" (
    echo [WARN] Gilligan's Island not found at %GILLIGAN_DIR%
    echo        Update GILLIGAN_DIR in this script if it's in a different location.
    echo        Skipping Gilligan's Island startup — start it manually.
    goto :skip_gilligan
)

echo Starting Gilligan's Island on port 3000...
start "Gilligan's Island" cmd /k "cd /d %GILLIGAN_DIR% && npm start"
echo [OK] Gilligan's Island window opened
timeout /t 3 /nobreak >nul

REM Quick health check
curl -sf http://localhost:3000/api/summary >nul 2>&1
if errorlevel 1 (
    echo [WARN] Gilligan's Island may still be starting — check its window
) else (
    echo [OK] Gilligan's Island responding on port 3000
)

:skip_gilligan

REM ── Start ngrok ─────────────────────────────────────────────────
echo.
ngrok --version >nul 2>&1
if errorlevel 1 (
    echo [WARN] ngrok not in PATH — skipping.
    echo        Start it manually:  ngrok http 3978
    echo        Then update your Azure Bot messaging endpoint to:
    echo          https://<your-ngrok-id>.ngrok-free.app/api/messages
    goto :skip_ngrok
)

echo Starting ngrok tunnel to Teams bot port 3978...
start "ngrok — Teams Bot Tunnel" cmd /k "ngrok http 3978"
echo [OK] ngrok window opened
timeout /t 4 /nobreak >nul

REM Try to get ngrok URL from local API
for /f "delims=" %%u in ('curl -sf http://127.0.0.1:4040/api/tunnels 2^>nul ^| python -c "import sys,json; t=json.load(sys.stdin).get(\"tunnels\",[{}]); print(next((x[\"public_url\"] for x in t if x.get(\"proto\")==\"https\"),\"\"))" 2^>nul') do set NGROK_URL=%%u

if not "%NGROK_URL%"=="" (
    echo.
    echo  !! COPY THIS URL into your Azure Bot messaging endpoint:
    echo     %NGROK_URL%/api/messages
    echo.
) else (
    echo     Check the ngrok window for your public URL.
    echo     Set Azure Bot messaging endpoint to: https://xxxx.ngrok-free.app/api/messages
)

:skip_ngrok

REM ── Summary ─────────────────────────────────────────────────────
echo.
echo  ============================================================
echo   Host services running
echo  ============================================================
echo.
echo   Gilligan's Island dashboard:  http://localhost:3000
echo   ngrok inspector:              http://localhost:4040
echo.
echo   VMs should connect to this machine at:
if defined GILLIGAN_HOST_IP (
    echo     %GILLIGAN_HOST_IP%:3000   ^(Gilligan's Island^)
) else (
    echo     192.168.56.1:3000   ^(Gilligan's Island — check VirtualBox host-only IP^)
)
echo.
echo   Keep this window open. Press any key to stop all services.
pause >nul

echo Stopping services...
taskkill /fi "WindowTitle eq Gilligan's Island*" /f >nul 2>&1
taskkill /fi "WindowTitle eq ngrok*" /f >nul 2>&1
echo Done.
