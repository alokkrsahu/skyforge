#!/bin/bash
# Terminal 2 — Gazebo 3D GUI window
# Connects to the physics server started by t1_sitl.sh
# Logs saved to /tmp/gz_gui.log for debugging

LOG=/tmp/gz_gui.log
export PATH="/opt/homebrew/bin:$PATH"

# The GUI is launched separately from the physics server, so it does NOT inherit
# the model/world resource paths PX4 sets for the server. Without them the GUI
# can't resolve model://x500_base/... meshes & textures and renders drones as
# bare markers, spamming "Unable to find file" errors. Source PX4's generated
# gz_env.sh (same GZ_SIM_RESOURCE_PATH / plugin paths / macOS DYLD fallback the
# server uses); fall back to constructing the paths if it's not present.
PX4_DIR="${PX4_DIR:-$HOME/src/PX4-Autopilot}"
GZ_ENV="$PX4_DIR/build/px4_sitl_default/rootfs/gz_env.sh"
if [ -f "$GZ_ENV" ]; then
    # shellcheck disable=SC1090
    source "$GZ_ENV"
else
    export GZ_SIM_RESOURCE_PATH="$PX4_DIR/Tools/simulation/gz/models:$PX4_DIR/Tools/simulation/gz/worlds${GZ_SIM_RESOURCE_PATH:+:$GZ_SIM_RESOURCE_PATH}"
fi

echo "========================================="
echo " Gazebo Harmonic GUI"
echo " Log: $LOG"
echo "========================================="

# Check server is running before opening GUI
echo "[t2] Checking Gazebo server is ready..."
ATTEMPTS=20
while [ $ATTEMPTS -gt 0 ]; do
    # Match ANY world (arena-agnostic) — the GUI attaches to whatever t1 launched.
    if GZ_IP=127.0.0.1 gz topic -l 2>/dev/null | grep -qE "^/world/.+/clock"; then
        _world="$(GZ_IP=127.0.0.1 gz topic -l 2>/dev/null | grep -m1 -E '^/world/.+/clock' | sed 's#/world/##; s#/clock##')"
        echo "[t2] Server found (world: ${_world:-?}). Opening Gazebo window..."
        break
    fi
    ATTEMPTS=$((ATTEMPTS-1))
    if [ $ATTEMPTS -eq 0 ]; then
        echo "ERROR: Gazebo server not found."
        echo "       Make sure t1_sitl.sh is running and has reached:"
        echo "       INFO [gz_bridge] world: <arena>, model: x500_0"
        exit 1
    fi
    echo "[t2] Waiting for server... ($ATTEMPTS attempts left)"
    sleep 1
done

echo "[t2] Use mouse to navigate: left-click drag = rotate, scroll = zoom"
echo "[t2] Press F in Gazebo to focus on the drone if not visible"
echo ""

exec env GZ_IP=127.0.0.1 gz sim -g --verbose=2 2>&1 | tee "$LOG"
