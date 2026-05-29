#!/usr/bin/env bash
# t5_skyforge.sh — Play a Skyforge show file via PX4 SITL.
# Run in a third terminal after t1_sitl.sh and t2_gazebo_gui.sh are up.
#
# Usage:
#   ./t5_skyforge.sh                                    — default 4-drone show
#   ./t5_skyforge.sh ../shows/my_show.skyforge.json     — any show file
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PX4_DIR="$HOME/src/PX4-Autopilot"
VENV="$PX4_DIR/.venv"

# ── Preflight checks ──────────────────────────────────────────────────────────
if [[ ! -d "$VENV" ]]; then
    echo "[t5] ERROR: venv not found at $VENV"
    echo "       Run: python3 -m venv $VENV && source $VENV/bin/activate && pip install mavsdk scipy msgpack"
    exit 1
fi

source "$VENV/bin/activate"

# ── Select show file ──────────────────────────────────────────────────────────
DEFAULT_SHOW="$SCRIPT_DIR/../shows/four_drone_demo.skyforge.json"
SHOW_FILE="${1:-$DEFAULT_SHOW}"

if [[ ! -f "$SHOW_FILE" ]]; then
    echo "[t5] ERROR: Show file not found: $SHOW_FILE"
    exit 1
fi

# ── Read drone count from the show file ──────────────────────────────────────
N=$(python3 -c "
import sys
sys.path.insert(0, '$SCRIPT_DIR/..')
from core.show_format.reader import from_json, from_msgpack
show = from_json('$SHOW_FILE') if '$SHOW_FILE'.endswith('.json') else from_msgpack('$SHOW_FILE')
print(show.metadata.n_drones)
")

echo "[t5] Show: $(basename "$SHOW_FILE") — $N drones"
echo "[t5] Waiting for $N PX4 instances to report 'home set'..."

# ── Wait for all N drones ─────────────────────────────────────────────────────
for (( i=0; i<N; i++ )); do
    while true; do
        if grep -q "Startup script returned successfully" "/tmp/px4_sitl_${i}.log" 2>/dev/null; then
            echo "[t5] Drone $i ready"
            break
        elif grep -q "Startup script returned with return value:" "/tmp/px4_sitl_${i}.log" 2>/dev/null; then
            echo "[t5] ERROR: Drone $i rcS failed — restart t1_sitl.sh before continuing"
            exit 1
        fi
        sleep 1
    done
done
echo "[t5] All $N drones ready"

# ── Launch show ───────────────────────────────────────────────────────────────
echo "[t5] Launching show: $(basename "$SHOW_FILE")"
echo "[t5] Press Ctrl-C to abort."
echo ""

cd "$SCRIPT_DIR"
exec python3 -u run_skyforge.py "$SHOW_FILE" 2> >(grep -v "Sending message failed\|not-existing command\|callback queue slow" >&2)
