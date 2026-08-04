#!/bin/bash

GREEN='\033[0;32m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
RESET='\033[0m'

ok()   { echo -e "${GREEN}$1${RESET}"; }
warn() { echo -e "${YELLOW}$1${RESET}"; }
err()  { echo -e "${RED}$1${RESET}"; }

# --- Bot status ---
echo "=== Bot Status ==="

logfile_for() {
    case $1 in
        print_bot)   echo "logs/bot.log" ;;
        monitor_bot) echo "logs/monitor.log" ;;
        gallery_bot) echo "logs/gallery.log" ;;
    esac
}

check_bot() {
    local name=$1
    local pid=$2
    local logfile
    logfile=$(logfile_for "$name")

    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        ok "${name} RUNNING (PID ${pid})"
        if [ -f "$logfile" ]; then
            echo "  Last 3 log lines:"
            tail -3 "$logfile" | sed 's/^/    /'
        fi
    else
        err "${name} STOPPED"
    fi
    echo
}

# Try .pids first (manual ./run.sh), fall back to launchctl (auto-start)
launchd_pid() {
    launchctl list 2>/dev/null | awk -v label="com.local.zapzap.$1" '$3 == label && $1 != "-" { print $1 }'
}

if [ -f ".pids" ]; then
    PRINT_PID=$(grep "^print_bot=" .pids 2>/dev/null | cut -d= -f2 | tr -d '\r\n ')
    MONITOR_PID=$(grep "^monitor_bot=" .pids 2>/dev/null | cut -d= -f2 | tr -d '\r\n ')
    GALLERY_PID=$(grep "^gallery_bot=" .pids 2>/dev/null | cut -d= -f2 | tr -d '\r\n ')
else
    PRINT_PID=""
    MONITOR_PID=""
    GALLERY_PID=""
fi

[ -z "$PRINT_PID" ]   && PRINT_PID=$(launchd_pid print_bot)
[ -z "$MONITOR_PID" ] && MONITOR_PID=$(launchd_pid monitor_bot)
[ -z "$GALLERY_PID" ] && GALLERY_PID=$(launchd_pid gallery_bot)

check_bot print_bot "$PRINT_PID"
check_bot monitor_bot "$MONITOR_PID"
check_bot gallery_bot "$GALLERY_PID"

# --- Printer status (via the cross-platform printing abstraction) ---
echo "=== Printer Status ==="
PY=".venv/bin/python"
[ -x "$PY" ] || PY="python3"
if PRINTER_STATUS=$("$PY" -m printing status 2>/dev/null); then
    ok "$PRINTER_STATUS"
elif [ -n "$PRINTER_STATUS" ]; then
    err "$PRINTER_STATUS"
else
    err "Printer NOT DETECTED"
fi
echo

# --- Queue status ---
echo "=== Queue ==="
if [ -f "queue.jsonl" ]; then
    PENDING=$(grep -c '"status":[ ]*"pending"' queue.jsonl 2>/dev/null || echo 0)
    echo "Queue: ${PENDING} jobs pending"
else
    echo "Queue: empty"
fi
echo

# --- Log sizes ---
echo "=== Log Sizes ==="
for f in print_log.jsonl gallery_log.jsonl queue.jsonl; do
    if [ -f "$f" ]; then
        ls -lh "$f" | awk '{print $9, $5}'
    else
        echo "$f not yet created"
    fi
done
echo

# --- Uptime ---
echo "=== Uptime ==="
if [ -f ".pids" ]; then
    STARTED=$(stat -f "%Sm" -t "%d %b %Y %H:%M" .pids 2>/dev/null)
    echo "Bots started: ${STARTED}"
else
    echo "Bots not currently running."
fi
