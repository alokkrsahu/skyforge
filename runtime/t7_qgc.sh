#!/bin/bash
# Terminal 7 — QGroundControl (monitor + GCS for SITL / HITL / real)
#
# QGC auto-connects to UDP 14550, where PX4 sends its GCS stream — every SITL
# instance converges there, and QGC disambiguates them by MAV_SYS_ID (= instance+1).
# Run the show with SKYFORGE_GCS=qgc so Skyforge SKIPS its own beacon and lets QGC
# own 14550 and supply the GCS heartbeat PX4's arm gate wants. (Skyforge keeps the
# onboard control links 15000+i, separate from QGC.)
#
# Usage:
#   ./t1_sitl.sh N                          # T1: start SITL first
#   ./t7_qgc.sh                             # this — opens QGroundControl
#   SKYFORGE_GCS=qgc ./t6_commander.sh N    # T3: run the show (beacon disabled)
#
# Env: QGC_APP overrides the macOS app name (default "QGroundControl").

case "${1:-}" in -h|--help) sed -n '2,15p' "$0"; exit 0;; esac

APP="${QGC_APP:-QGroundControl}"
echo "[t7] Opening ${APP} (auto-connects UDP 14550)..."
echo "[t7] Then run the show with:  SKYFORGE_GCS=qgc ./t6_commander.sh N   (or t5_skyforge.sh)"
echo "[t7] (QGC must be up before arming in qgc mode — it is now the GCS heartbeat.)"
if ! open -a "$APP" 2>/dev/null; then
    echo "[t7] ERROR: could not open '$APP'."
    echo "      Set QGC_APP to the installed app name, or launch QGroundControl manually"
    echo "      (it auto-connects to UDP 14550 either way)."
    exit 1
fi
