#!/bin/bash
# Terminal 6 — Interactive Drone Commander
# Requires t1_sitl.sh N and t2_gazebo_gui.sh running first.
#
# Usage:
#   ./t6_commander.sh [N]   # default 10 drones

set -euo pipefail
export PATH="/opt/homebrew/bin:$PATH"

PX4_DIR="$HOME/src/PX4-Autopilot"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
N="${1:-10}"

echo "============================================="
echo " Drone Commander — ${N} drones"
echo " Commands: takeoff / land / circle / text A /"
echo "           grid / star / spiral / v / move ..."
echo "============================================="

# Kill leftover commander sessions AND mavsdk_server processes from previous
# runs. A previous run_commander.py left running will keep respawning servers on
# the same ports and fight this new session — both sessions then kill each
# other's servers, so every drone dies with "Socket closed" at takeoff. Kill the
# old Python commanders FIRST (so they can't respawn) then the servers. SIGKILL
# + 2 s wait lets the OS release the UDP ports before we spawn fresh servers.
pkill -9 -f "run_commander.py" 2>/dev/null || true
pkill -9 -f "mavsdk_server"    2>/dev/null || true
sleep 2.0

# Wait for all N PX4 instances to finish startup
echo "[t6] Waiting for ${N} PX4 instances to be ready..."
ATTEMPTS=120
while [ "$ATTEMPTS" -gt 0 ]; do
    READY=0
    for (( i=0; i<N; i++ )); do
        if grep -q "Startup script returned successfully" "/tmp/px4_sitl_${i}.log" 2>/dev/null; then
            READY=$((READY + 1))
        fi
    done
    if [ "$READY" -ge "$N" ]; then
        echo "[t6] All ${N} drones ready"
        break
    fi
    echo "[t6] Waiting... ${READY}/${N} ready"
    sleep 2
    ATTEMPTS=$((ATTEMPTS - 1))
done

if [ "$ATTEMPTS" -eq 0 ]; then
    echo "[t6] ERROR: Timed out waiting for PX4 instances. Is t1_sitl.sh running?"
    exit 1
fi

source "$PX4_DIR/.venv/bin/activate"
cd "$SCRIPT_DIR"

echo "[t6] Starting commander (type 'help' for commands)..."
exec python3 -u run_commander.py "$N" 2> >(grep -v "Sending message failed\|not-existing command\|callback queue slow" >&2)
