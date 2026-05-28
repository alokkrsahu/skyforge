#!/bin/bash
# Terminal 1 — PX4 SITL x N + Gazebo physics server
#
# Usage:
#   ./t1_sitl.sh       — default 4 drones
#   ./t1_sitl.sh 10    — 10 drones (recommended for 14-core Mac)
#   ./t1_sitl.sh 16    — 16 drones (may lag; monitor Gazebo frame rate)
#
# Logs: /tmp/px4_sitl_0.log .. /tmp/px4_sitl_N-1.log

export PATH="/opt/homebrew/bin:$PATH"
PX4_DIR="$HOME/src/PX4-Autopilot"
WORKDIR="$PX4_DIR/build/px4_sitl_default/rootfs"
PX4_BIN="$PX4_DIR/build/px4_sitl_default/bin/px4"

N="${1:-4}"   # number of drones (default 4)

echo "========================================="
echo " PX4 SITL x${N} + Gazebo Server"
echo " Logs: /tmp/px4_sitl_0.log .. _$((N-1)).log"
echo "========================================="

echo "[t1] Cleaning up stale processes..."
pkill -9 -f "bin/px4"  2>/dev/null
pkill -9 -f "gz sim"   2>/dev/null
rm -f /tmp/px4_lock-*  /tmp/px4_sitl_*.log
sleep 1

if [ ! -f "$PX4_BIN" ]; then
    echo "ERROR: PX4 binary not found. Run: make px4_sitl_default"
    exit 1
fi

source "$PX4_DIR/.venv/bin/activate"
cd "$WORKDIR"

# ── Compute spawn grid ────────────────────────────────────────────────────────
# Drones arranged in a square-ish grid, 2 m spacing.
# Gazebo coord: x=East, y=North → pose "x,y,z,0,0,0"
COLS=$(python3 -c "import math; print(math.ceil(math.sqrt($N)))")

poses=()
for (( i=0; i<N; i++ )); do
    col=$(( i % COLS ))
    row=$(( i / COLS ))
    x=$(python3 -c "print(${col} * 2.0)")
    y=$(python3 -c "print(${row} * 2.0)")
    poses+=("${x},${y},0,0,0,0")
done

# ── Instance 0: starts the Gazebo server ─────────────────────────────────────
echo "[t1] Starting instance 0 (Gazebo server)..."
env HEADLESS=1 PX4_SIM_MODEL=gz_x500 GZ_IP=127.0.0.1 \
    PX4_GZ_MODEL_POSE="${poses[0]}" \
    "$PX4_BIN" -i 0 > /tmp/px4_sitl_0.log 2>&1 &

echo "[t1] Waiting 8 s for Gazebo server to init..."
sleep 8

# ── Remaining instances ───────────────────────────────────────────────────────
for (( i=1; i<N; i++ )); do
    echo "[t1] Starting instance $i (pose: ${poses[$i]})..."
    env HEADLESS=1 PX4_SIM_MODEL=gz_x500 GZ_IP=127.0.0.1 \
        PX4_GZ_MODEL_POSE="${poses[$i]}" \
        "$PX4_BIN" -i $i > "/tmp/px4_sitl_$i.log" 2>&1 &
    sleep 3
done

echo ""
echo "[t1] All $N instances launched. Waiting for home position..."
echo ""

# ── Poll until all N have a home position ─────────────────────────────────────
while true; do
    READY=0
    for (( i=0; i<N; i++ )); do
        grep -q "home set" "/tmp/px4_sitl_$i.log" 2>/dev/null && (( READY++ ))
    done
    echo "[t1] $READY/$N drones ready..."
    if [ "$READY" -eq "$N" ]; then
        echo ""
        echo "[t1] All $N drones ready!"
        echo "[t1] -> Start t2_gazebo_gui.sh in Terminal 2"
        echo "[t1] -> Start t5_skyforge.sh in Terminal 3"
        break
    fi
    sleep 3
done

wait
