#!/usr/bin/env bash
# Upload-and-go SITL demo (ROADMAP #1): export per-drone trajectory slices, then launch
# ONE on-board agent per PX4 instance, all sharing a single start instant. Each agent flies
# its own validated slice autonomously — the model that scales to thousands.
#
# Prereqs: PX4 SITL running with >= N instances (./t1_sitl.sh N), and a compiled show.
# Usage:   ./t8_agents.sh <show.skyforge.json> [N]
set -euo pipefail

SHOW="${1:?usage: ./t8_agents.sh <show.skyforge.json> [N]}"
N="${2:-4}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIR="$(mktemp -d)"
BASE="$(basename "$SHOW" | sed 's/\.skyforge.*//')"

echo "[t8] Exporting $N per-drone slices to $DIR ..."
python3 "$ROOT/cli.py" export "$SHOW" --all -o "$DIR"

# One shared start instant (15 s out) so every agent begins together.
T0="$(python3 -c 'import time; print(time.time() + 15)')"
export SKYFORGE_T0_EPOCH="$T0" SKYFORGE_GCS=none
echo "[t8] Launching $N on-board agents — shared T0 in 15 s (SKYFORGE_T0_EPOCH=$T0)"

for i in $(seq 0 $((N - 1))); do
    ID="$(printf '%03d' "$i")"
    python3 "$ROOT/runtime/agent/onboard_agent.py" \
        --drone-id "$i" --trajectory "$DIR/$BASE.drone$ID.skyforge.json" &
done
wait
echo "[t8] All agents exited."
