#!/bin/bash

GREEN='\033[0;32m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
RESET='\033[0m'

ok()   { echo -e "${GREEN}$1${RESET}"; }
warn() { echo -e "${YELLOW}$1${RESET}"; }
err()  { echo -e "${RED}$1${RESET}"; }

# --- Check .env is configured ---
if [ ! -f ".env" ]; then
    err ".env not found. Run ./setup.sh first."
    exit 1
fi
set -a
. ./.env
set +a
if [ -z "$BOT_TOKEN" ] && [ -z "$PRINT_BOT_TOKEN" ]; then
    err ".env is not configured. Run setup.sh first."
    exit 1
fi
TOKEN_VALUE="${BOT_TOKEN:-$PRINT_BOT_TOKEN}"
if [ "$TOKEN_VALUE" = "YOUR_PRINT_BOT_TOKEN" ]; then
    err ".env is not configured. Run setup.sh first."
    exit 1
fi

# --- Check .venv exists ---
if [ ! -x ".venv/bin/python" ]; then
    err "Virtual environment not found. Run ./setup.sh first."
    exit 1
fi

# --- Check printer ---
if ! lpstat -p 2>/dev/null | grep -qi "MITSUBISHI"; then
    warn "Mitsubishi printer not detected — print jobs will fail."
    echo "Continuing anyway. Connect the printer and restart if needed."
fi

# --- Make sure logs directory exists ---
mkdir -p logs

# --- Load existing PIDs ---
read_pid() {
    grep "^$1=" .pids 2>/dev/null | cut -d= -f2 | tr -d '\r\n '
}

PRINT_PID=$(read_pid print_bot)
MONITOR_PID=$(read_pid monitor_bot)
GALLERY_PID=$(read_pid gallery_bot)
HUB_PID=$(read_pid hub)

# Start a bot if not already running. Sets PID via the named global var.
start_bot() {
    local label=$1
    local script=$2
    local logfile=$3
    local existing_pid=$4
    local pid_var=$5

    if [ -n "$existing_pid" ] && kill -0 "$existing_pid" 2>/dev/null; then
        echo "${label} already running (PID ${existing_pid})"
        eval "$pid_var=\"$existing_pid\""
        return
    fi

    nohup .venv/bin/python "$script" >> "$logfile" 2>&1 &
    local new_pid=$!
    ok "${label} started (PID ${new_pid}), logging to ${logfile}"
    eval "$pid_var=\"$new_pid\""
}

# The hub is optional and machine-specific: only the machine acting as the hub
# has a hub.env, so its presence is what decides whether to start one.
start_hub() {
    if [ ! -f "hub.env" ]; then
        return
    fi
    if [ -n "$HUB_PID" ] && kill -0 "$HUB_PID" 2>/dev/null; then
        echo "Hub already running (PID ${HUB_PID})"
        return
    fi
    nohup .venv/bin/python -m hub.main serve >> logs/hub.log 2>&1 &
    HUB_PID=$!
    local bind port
    bind=$(grep -E "^HUB_BIND=" hub.env | cut -d= -f2 | tr -d "\r\n ")
    port=$(grep -E "^HUB_PORT=" hub.env | cut -d= -f2 | tr -d "\r\n ")
    ok "Hub started (PID ${HUB_PID}), dashboard on http://${bind:-127.0.0.1}:${port:-8080}"
}

start_bot "Print bot"   bot.py     logs/bot.log     "$PRINT_PID"   PRINT_PID
start_bot "Monitor bot" monitor.py logs/monitor.log "$MONITOR_PID" MONITOR_PID
start_bot "Gallery bot" gallery.py logs/gallery.log "$GALLERY_PID" GALLERY_PID
start_hub

# --- Save all PIDs ---
{
    echo "print_bot=${PRINT_PID}"
    echo "monitor_bot=${MONITOR_PID}"
    echo "gallery_bot=${GALLERY_PID}"
    [ -n "$HUB_PID" ] && echo "hub=${HUB_PID}"
} > .pids

echo
echo "All bots running. Use ./status.sh to check health."
echo "Logs: logs/bot.log | logs/monitor.log | logs/gallery.log"
[ -f "hub.env" ] && echo "      logs/hub.log (hub)"
echo "To stop: ./stop.sh"
