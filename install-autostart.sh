#!/bin/bash
set -e

GREEN='\033[0;32m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
RESET='\033[0m'

ok()   { echo -e "${GREEN}$1${RESET}"; }
warn() { echo -e "${YELLOW}$1${RESET}"; }
err()  { echo -e "${RED}$1${RESET}"; }

PROJECT_DIR=$(cd "$(dirname "$0")" && pwd)
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
PYTHON="$PROJECT_DIR/.venv/bin/python"

if [ ! -x "$PYTHON" ]; then
    err "Virtual environment not found at $PYTHON. Run ./setup.sh first."
    exit 1
fi

# Stop any manually-started bots first to avoid duplicate instances
if [ -f "$PROJECT_DIR/.pids" ]; then
    echo "Stopping manually-started bots first..."
    "$PROJECT_DIR/stop.sh" || true
    echo
fi

mkdir -p "$LAUNCH_AGENTS_DIR"
mkdir -p "$PROJECT_DIR/logs"

# write_plist <name> <script-or-"-m module args"> <logfile>
write_plist() {
    local name=$1
    local script=$2
    local logfile=$3
    local args=""
    if [ "${script#-m }" != "$script" ]; then
        # module form: -m hub.main serve  ->  one <string> per token
        for tok in ${script}; do
            args="${args}        <string>${tok}</string>
"
        done
    else
        args="        <string>${PROJECT_DIR}/${script}</string>
"
    fi
    local label="com.local.zapzap.${name}"
    local plist="$LAUNCH_AGENTS_DIR/${label}.plist"

    cat > "$plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${label}</string>
    <key>ProgramArguments</key>
    <array>
        <string>${PYTHON}</string>
${args}    </array>
    <key>WorkingDirectory</key>
    <string>${PROJECT_DIR}</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>${PROJECT_DIR}/${logfile}</string>
    <key>StandardErrorPath</key>
    <string>${PROJECT_DIR}/${logfile}</string>
</dict>
</plist>
EOF

    # Reload (unload if already there, then load)
    launchctl unload "$plist" 2>/dev/null || true
    launchctl load "$plist"
    ok "${name}: ${plist}"
}

echo "Installing launchd agents..."
write_plist print_bot   bot.py     logs/bot.log
write_plist monitor_bot monitor.py logs/monitor.log
write_plist gallery_bot gallery.py logs/gallery.log

# The hub is optional: only the machine acting as the hub has a hub.env.
if [ -f "$PROJECT_DIR/hub.env" ]; then
    write_plist hub "-m hub.main serve" logs/hub.log
else
    echo "No hub.env - skipping the hub agent (this machine is a booth only)."
fi
echo

ok "Auto-start installed."
echo
echo "All three bots will now:"
echo "  • Start automatically when you log in."
echo "  • Restart automatically if they crash."
echo
echo "Verify they are running:"
echo "  launchctl list | grep zapzap"
echo "  ./status.sh"
echo
echo "Important: do NOT use ./run.sh while auto-start is installed —"
echo "it would create duplicate bot instances and cause Telegram errors."
echo "Use ./uninstall-autostart.sh first if you want to go back to manual."
