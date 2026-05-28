#!/bin/bash
# Terminal 2 — Gazebo 3D GUI window
# Connects to the physics server started by t1_sitl.sh
# Logs saved to /tmp/gz_gui.log for debugging

LOG=/tmp/gz_gui.log
export PATH="/opt/homebrew/bin:$PATH"

echo "========================================="
echo " Gazebo Harmonic GUI"
echo " Log: $LOG"
echo "========================================="

# Check server is running before opening GUI
echo "[t2] Checking Gazebo server is ready..."
ATTEMPTS=20
while [ $ATTEMPTS -gt 0 ]; do
    if GZ_IP=127.0.0.1 gz topic -l 2>/dev/null | grep -q "/world/default/clock"; then
        echo "[t2] Server found. Opening Gazebo window..."
        break
    fi
    ATTEMPTS=$((ATTEMPTS-1))
    if [ $ATTEMPTS -eq 0 ]; then
        echo "ERROR: Gazebo server not found."
        echo "       Make sure t1_sitl.sh is running and has reached:"
        echo "       INFO [gz_bridge] world: default, model: x500_0"
        exit 1
    fi
    echo "[t2] Waiting for server... ($ATTEMPTS attempts left)"
    sleep 1
done

echo "[t2] Use mouse to navigate: left-click drag = rotate, scroll = zoom"
echo "[t2] Press F in Gazebo to focus on the drone if not visible"
echo ""

exec env GZ_IP=127.0.0.1 gz sim -g --verbose=2 2>&1 | tee "$LOG"
