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
rm -f /tmp/px4_lock-*  /tmp/px4-sock-*  /tmp/px4_sitl_*.log
sleep 1

if [ ! -f "$PX4_BIN" ]; then
    echo "ERROR: PX4 binary not found. Run: make px4_sitl_default"
    exit 1
fi

source "$PX4_DIR/.venv/bin/activate"
cd "$WORKDIR"

# ── Compute all spawn poses in one Python call (avoids 2N subprocess starts) ──
# Drones in a square-ish grid, 2 m spacing. Gazebo coord: x=East, y=North.
# read loop, not mapfile — macOS /bin/bash is 3.2 and has no mapfile/readarray
poses=()
while IFS= read -r _pose; do poses+=("$_pose"); done < <(python3 - <<EOF
import math
n = $N
cols = math.ceil(math.sqrt(n))
for i in range(n):
    col, row = i % cols, i // cols
    print(f"{col * 2.0},{row * 2.0},0,0,0,0")
EOF
)

# ── Instance 0: starts the Gazebo server ─────────────────────────────────────
echo "[t1] Starting instance 0 (Gazebo server)..."
env HEADLESS=1 PX4_SIM_MODEL=gz_x500 GZ_IP=127.0.0.1 \
    PX4_GZ_MODEL_POSE="${poses[0]}" \
    "$PX4_BIN" -d -i 0 > /tmp/px4_sitl_0.log 2>&1 &

# Poll for Gazebo readiness instead of a fixed sleep — usually up in 3-5 s.
echo "[t1] Waiting for Gazebo world (instance 0)..."
for _ in $(seq 1 120); do
    grep -q "Gazebo world is ready" /tmp/px4_sitl_0.log 2>/dev/null && break
    sleep 0.5
done
sleep 1   # small cushion for gz_bridge to come up

# ── Remaining instances — small stagger avoids Gazebo spawn contention ────────
# Parallel model-spawn requests can make rcS fail (return value 2); 0.5 s is
# enough to serialise spawns while the launches still overlap. 4 s was overkill.
STAGGER="${T1_STAGGER:-0.5}"
for (( i=1; i<N; i++ )); do
    echo "[t1] Starting instance $i (pose: ${poses[$i]})..."
    env HEADLESS=1 PX4_SIM_MODEL=gz_x500 GZ_IP=127.0.0.1 \
        PX4_GZ_MODEL_POSE="${poses[$i]}" \
        "$PX4_BIN" -d -i $i > "/tmp/px4_sitl_$i.log" 2>&1 &
    sleep "$STAGGER"
done

echo ""
echo "[t1] All $N instances launched. Waiting for home position..."
echo ""

# ── Helper: start one PX4 instance ───────────────────────────────────────────
start_instance() {
    local i=$1
    echo "[t1] (Re)starting instance $i (pose: ${poses[$i]})..."
    # Kill any existing process for this instance. Anchor the trailing "-i $i"
    # with $ so e.g. instance 3 doesn't also match instance 36 — and so we don't
    # rely on \b, which BSD/macOS pkill does NOT support (the old pattern matched
    # nothing, so the hung process survived).
    pkill -9 -f "px4 -d -i $i$" 2>/dev/null || true
    sleep 1
    # Clear the stale lock AND IPC socket. Without this the relaunched process
    # logs "PX4 server already running for instance $i", attaches to the dead
    # socket instead of running its startup script, and never becomes ready.
    rm -f "/tmp/px4_lock-$i" "/tmp/px4-sock-$i" "/tmp/px4_sitl_$i.log"
    # A hung instance may have already spawned its Gazebo model; remove it so the
    # relaunched instance can re-spawn. Otherwise gz_bridge fails with the model
    # already present ("Task already running" → rcS return value 256). Best-effort.
    GZ_IP=127.0.0.1 gz service -s /world/default/remove \
        --reqtype gz.msgs.Entity --reptype gz.msgs.Boolean --timeout 2000 \
        --req "name: \"x500_$i\" type: MODEL" >/dev/null 2>&1 || true
    sleep 0.5
    env HEADLESS=1 PX4_SIM_MODEL=gz_x500 GZ_IP=127.0.0.1 \
        PX4_GZ_MODEL_POSE="${poses[$i]}" \
        "$PX4_BIN" -d -i $i > "/tmp/px4_sitl_$i.log" 2>&1 &
}

# ── Poll until all N have completed startup; retry on rcS failure ─────────────
# "Startup script returned successfully" is logged after all MAVLink interfaces
# start — a stronger signal than "home set" which appears earlier in rcS.
# Restart on rcS failure OR a silent hang (no marker within HANG_S seconds),
# up to 3 times each; then give up so the loop can never spin forever.
declare -a retries deadline
HANG_S="${T1_HANG_S:-50}"
for (( i=0; i<N; i++ )); do deadline[$i]=$(( SECONDS + HANG_S )); done
while true; do
    READY=0; FAILED=0; WAITING=""
    for (( i=0; i<N; i++ )); do
        log="/tmp/px4_sitl_$i.log"
        if grep -q "Startup script returned successfully" "$log" 2>/dev/null; then
            (( READY++ )); continue
        fi
        bad=""
        if grep -q "Startup script returned with return value:" "$log" 2>/dev/null; then
            bad="rcS error"
        elif [ "$SECONDS" -gt "${deadline[$i]}" ]; then
            bad="hung ${HANG_S}s"
        fi
        if [ -n "$bad" ]; then
            if [ "${retries[$i]:-0}" -lt 3 ]; then
                retries[$i]=$(( ${retries[$i]:-0} + 1 ))
                echo "[t1] Instance $i $bad — restart ${retries[$i]}/3..."
                start_instance $i
                deadline[$i]=$(( SECONDS + HANG_S ))
                WAITING="$WAITING $i"
            else
                (( FAILED++ )); WAITING="$WAITING $i(FAILED)"
            fi
        else
            WAITING="$WAITING $i"
        fi
    done
    echo "[t1] $READY/$N drones fully started${WAITING:+ (waiting:$WAITING)}..."
    if [ "$(( READY + FAILED ))" -eq "$N" ]; then
        echo ""
        if [ "$FAILED" -gt 0 ]; then
            echo "[t1] $READY/$N ready, $FAILED FAILED after 3 retries — check /tmp/px4_sitl_*.log"
        else
            echo "[t1] All $N drones ready!"
        fi
        echo "[t1] -> Start t2_gazebo_gui.sh in Terminal 2"
        echo "[t1] -> Start t5_skyforge.sh in Terminal 3"
        break
    fi
    sleep 1
done

wait
