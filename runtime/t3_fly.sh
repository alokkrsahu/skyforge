#!/bin/bash
# Terminal 3 — Autonomous flight via MAVSDK (4 drones, separate processes)
# Each drone runs in its own Python process to avoid asyncio blocking.
# Logs: /tmp/px4_drone_0.log .. /tmp/px4_drone_3.log

PX4_DIR="$HOME/src/PX4-Autopilot"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "========================================="
echo " MAVSDK Autonomous Flight — 4 Drones"
echo " Logs: /tmp/px4_drone_0.log .. _3.log"
echo "========================================="

source "$PX4_DIR/.venv/bin/activate"

# Wait for all 4 instances to have a home position
echo "[t3] Waiting for all 4 PX4 instances to be ready..."
ATTEMPTS=60
while [ $ATTEMPTS -gt 0 ]; do
    READY=$(grep -l "home set" /tmp/px4_sitl_0.log /tmp/px4_sitl_1.log /tmp/px4_sitl_2.log /tmp/px4_sitl_3.log 2>/dev/null | wc -l | tr -d ' ')
    if [ "$READY" -eq 4 ]; then
        echo "[t3] All 4 drones ready. Starting flight..."
        break
    fi
    ATTEMPTS=$((ATTEMPTS-1))
    if [ $ATTEMPTS -eq 0 ]; then
        echo "ERROR: Only $READY/4 drones ready after 60 seconds."
        echo "       Check /tmp/px4_sitl_{0,1,2,3}.log for errors."
        exit 1
    fi
    echo "[t3] $READY/4 drones ready... ($ATTEMPTS attempts left)"
    sleep 1
done

echo ""
# Launch one Python process per drone — avoids asyncio event-loop blocking
for i in 0 1 2 3; do
    PORT=$((14540 + i))
    echo "[t3] Launching drone $i on port $PORT..."
    python3 -u "$SCRIPT_DIR/fly_single.py" $i $PORT > "/tmp/px4_drone_$i.log" 2>&1 &
done

echo "[t3] All 4 drone processes launched."
echo "[t3] Follow live output: tail -f /tmp/px4_drone_0.log /tmp/px4_drone_1.log /tmp/px4_drone_2.log /tmp/px4_drone_3.log"
echo ""
wait
echo "[t3] All drones finished."
