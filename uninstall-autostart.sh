#!/bin/bash

GREEN='\033[0;32m'
YELLOW='\033[0;33m'
RESET='\033[0m'

ok()   { echo -e "${GREEN}$1${RESET}"; }
warn() { echo -e "${YELLOW}$1${RESET}"; }

LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"

remove_plist() {
    local name=$1
    local label="com.local.zapzap.${name}"
    local plist="$LAUNCH_AGENTS_DIR/${label}.plist"

    if [ -f "$plist" ]; then
        launchctl unload "$plist" 2>/dev/null || true
        rm -f "$plist"
        ok "${name}: removed"
    else
        warn "${name}: not installed"
    fi
}

echo "Removing launchd agents..."
remove_plist print_bot
remove_plist monitor_bot
remove_plist gallery_bot
remove_plist hub          # no-op on booths that never ran one
echo
ok "Auto-start removed. Bots will no longer start at login."
echo "Use ./run.sh to start them manually when needed."
