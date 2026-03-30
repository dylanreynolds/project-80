#!/usr/bin/env bash
# =============================================================================
# Gilligan's Island — Debian VM startup script
# Starts the orchestrator and teams-bot in separate terminal sessions.
#
# Run as your normal user (not root) from the it-automation directory.
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "=== Gilligan's Island — Debian VM startup ==="
echo "Project: $PROJECT_DIR"
echo ""

# ── Load env vars ─────────────────────────────────────────────────────────────
ENV_FILE="$SCRIPT_DIR/.env.gilligan"
if [[ ! -f "$ENV_FILE" ]]; then
    echo "ERROR: $ENV_FILE not found. Copy and fill in gilligan/.env.gilligan first."
    exit 1
fi
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

# Validate required vars
if [[ "$COMMAND_SIGNING_SECRET" == "REPLACE_WITH_GENERATED_SECRET" ]]; then
    echo "ERROR: COMMAND_SIGNING_SECRET not set. Generate one with:"
    echo "  python3 -c \"import secrets; print(secrets.token_hex(32))\""
    exit 1
fi

# ── Python virtual environments ───────────────────────────────────────────────
setup_venv() {
    local dir="$1"
    local venv="$dir/.venv"
    if [[ ! -d "$venv" ]]; then
        echo "Creating venv in $dir ..."
        python3 -m venv "$venv"
    fi
    source "$venv/bin/activate"
    pip install -q --upgrade pip
    pip install -q -r "$dir/requirements.txt"
    deactivate
}

echo "Setting up orchestrator dependencies..."
setup_venv "$PROJECT_DIR/orchestrator"

echo "Setting up teams-bot dependencies..."
setup_venv "$PROJECT_DIR/teams-bot"

# ── Start orchestrator ────────────────────────────────────────────────────────
echo ""
echo "Starting orchestrator on port ${PORT:-8000}..."
(
    cd "$PROJECT_DIR/orchestrator"
    source .venv/bin/activate
    python app.py
) &
ORCHESTRATOR_PID=$!
echo "Orchestrator PID: $ORCHESTRATOR_PID"

# Give orchestrator a moment to bind its port
sleep 2

# ── Start teams-bot ───────────────────────────────────────────────────────────
echo "Starting teams-bot on port 3978..."
(
    cd "$PROJECT_DIR/teams-bot"
    source .venv/bin/activate
    python app.py
) &
BOT_PID=$!
echo "Teams-bot PID: $BOT_PID"

# ── Startup check ─────────────────────────────────────────────────────────────
sleep 3
echo ""
echo "Health check..."
curl -sf "http://127.0.0.1:${PORT:-8000}/health" && echo "" || echo "WARNING: orchestrator not responding yet"

echo ""
echo "=== All services started ==="
echo ""
echo "  Orchestrator:   http://127.0.0.1:${PORT:-8000}"
echo "  Teams bot:      http://127.0.0.1:3978"
echo "  Gilligan's IS:  $GILLIGAN_URL  (running on host laptop)"
echo ""
echo "  Job queue:      http://127.0.0.1:${PORT:-8000}/jobs"
echo "                  (requires X-API-Key: $ORCHESTRATOR_API_KEY)"
echo ""
echo "  ngrok (in another terminal):"
echo "    ngrok http 3978    # exposes teams-bot for Bot Framework"
echo "    # then update Azure bot messaging endpoint to the ngrok URL/api/messages"
echo ""
echo "Press Ctrl+C to stop all services."

# Wait for either process to exit, then kill both
wait $ORCHESTRATOR_PID $BOT_PID
