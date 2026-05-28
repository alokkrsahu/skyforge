#!/bin/bash
# Terminal 4 — Choreographed drone show
# Runs after t1_sitl.sh (all 4 drones ready) + t2_gazebo_gui.sh
# Implements: APF collision avoidance, Bézier paths, Virtual Structure formations, barrier sync

PX4_DIR="$HOME/src/PX4-Autopilot"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "========================================="
echo " Drone Show — 7 Acts, ~2 minutes"
echo "========================================="

source "$PX4_DIR/.venv/bin/activate"

# Wait for all 4 PX4 instances to have home position
echo "[t4] Waiting for all 4 drones to be ready..."
ATTEMPTS=60
while [ $ATTEMPTS -gt 0 ]; do
    READY=$(grep -l "home set" /tmp/px4_sitl_0.log /tmp/px4_sitl_1.log \
            /tmp/px4_sitl_2.log /tmp/px4_sitl_3.log 2>/dev/null | wc -l | tr -d ' ')
    if [ "$READY" -eq 4 ]; then
        echo "[t4] All 4 drones ready. Launching show..."
        break
    fi
    ATTEMPTS=$((ATTEMPTS-1))
    [ $ATTEMPTS -eq 0 ] && { echo "ERROR: Only $READY/4 drones ready."; exit 1; }
    echo "[t4] $READY/4 ready... ($ATTEMPTS left)"
    sleep 1
done

echo ""
cd "$SCRIPT_DIR"
python3 -u run_show.py
