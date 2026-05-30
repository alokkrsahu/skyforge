#!/bin/bash
# Terminal 1 — PX4 SITL x N + Gazebo physics server
#
# Usage:
#   ./t1_sitl.sh [N] [arena]      (run with -h to list available arenas)
#   ./t1_sitl.sh 10               — 10 drones, default arena
#   ./t1_sitl.sh 8 walls          — 8 drones in the 'walls' world
#
# Only t1 takes the arena; t2 and the runtime auto-detect the running world.
# Logs: /tmp/px4_sitl_0.log .. /tmp/px4_sitl_N-1.log

export PATH="/opt/homebrew/bin:$PATH"
PX4_DIR="$HOME/src/PX4-Autopilot"
WORKDIR="$PX4_DIR/build/px4_sitl_default/rootfs"
PX4_BIN="$PX4_DIR/build/px4_sitl_default/bin/px4"

N="${1:-4}"            # number of drones (default 4)
arena="${2:-default}"  # Gazebo world / arena (default: the deployed forest world)
PX4_GZ_WORLDS="${PX4_GZ_WORLDS:-$PX4_DIR/Tools/simulation/gz/worlds}"

usage() {
    echo "Usage: ./t1_sitl.sh [N] [arena]"
    echo "  N      number of drones (default 4)"
    echo "  arena  Gazebo world to load (default 'default'). Available:"
    for _w in "$PX4_GZ_WORLDS"/*.sdf; do
        [ -e "$_w" ] || continue
        _name="$(basename "$_w" .sdf)"
        case "$_name" in
            default) _note="DART 100Hz, forest scenery (recommended)";;
            *)       _note="ODE physics — large fleets (>~40) may crash";;
        esac
        case "$_name" in
            default|forest|baylands|underwater) _note="$_note; Fuel assets download on first run";;
        esac
        printf "           %-16s %s\n" "$_name" "$_note"
    done
    echo "  -h, --help   show this help"
    echo
    echo "Only t1 takes the arena; t2 + the runtime auto-detect the running world."
}

# Help BEFORE the destructive kill block below, so -h never tears down a stack.
for _a in "$1" "$2"; do
    case "$_a" in -h|--help) usage; exit 0;; esac
done

if [ ! -f "$PX4_GZ_WORLDS/$arena.sdf" ]; then
    echo "ERROR: arena '$arena' not found ($PX4_GZ_WORLDS/$arena.sdf)."
    echo
    usage
    exit 1
fi
export PX4_GZ_WORLD="$arena"

# Warn (never block) on the chosen arena's caveats.
if [ "$arena" != "default" ] && [ "$N" -gt 40 ]; then
    echo "[t1] WARNING: '$arena' uses ODE physics; >~40 drones may hit the ODE overflow crash. Proceeding."
fi
case "$arena" in
    forest|baylands|underwater)
        echo "[t1] NOTE: arena '$arena' pulls Fuel models (downloads on first run; needs internet).";;
esac

echo "========================================="
echo " PX4 SITL x${N} + Gazebo Server  (arena: ${arena})"
echo " Logs: /tmp/px4_sitl_0.log .. _$((N-1)).log"
echo "========================================="

echo "[t1] Full clean restart — clearing the previous flight stack..."
# Kill any OTHER t1_sitl.sh watchdog (NOT this invocation — excluding $$ avoids
# self-kill). Multiple watchdogs fight over instances and flap the GCS link.
for _pid in $(pgrep -f "t1_sitl.sh" 2>/dev/null); do
    [ "$_pid" = "$$" ] && continue
    kill -9 "$_pid" 2>/dev/null || true
done
# Kill the whole previous flight stack: PX4 + Gazebo + every mavsdk_server and
# Python runtime session. Orphaned mavsdk_servers / commanders rebind the same
# gRPC+UDP ports and cause "Connection reset by peer" / "Socket closed" at arm on
# the next run; a stale PX4 keeps the drained per-flight battery (→ arm failsafe).
# Restarting t1 gives a fresh sim (fresh battery) every time — the supported way to
# fly again. NOTE: this also stops any running t5/t6 session; relaunch it after.
pkill -9 -f "bin/px4"        2>/dev/null
pkill -9 -f "gz sim"         2>/dev/null
pkill -9 -f "mavsdk_server"  2>/dev/null
pkill -9 -f "run_commander"  2>/dev/null
pkill -9 -f "run_skyforge"   2>/dev/null
rm -f /tmp/px4_lock-*  /tmp/px4-sock-*  /tmp/px4_sitl_*.log
sleep 2   # let the OS release the UDP/gRPC ports before respawning

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

# Detect the running INNER world name (the gz namespace == the SDF's <world name>,
# which is NOT always the arena filename — frictionless.sdf and our default.sdf are
# inner-named "default"). Used for the model-remove in the restart helper below.
GZ_WORLD="$(GZ_IP=127.0.0.1 gz topic -l 2>/dev/null | grep -m1 -e '^/world/.*/clock' | sed 's#/world/##; s#/clock##')"
[ -z "$GZ_WORLD" ] && GZ_WORLD="$arena"
echo "[t1] Gazebo world: $GZ_WORLD"

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
    GZ_IP=127.0.0.1 gz service -s "/world/$GZ_WORLD/remove" \
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
