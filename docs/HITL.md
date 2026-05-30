# Hardware-in-the-loop (HITL) validation

HITL runs **PX4 firmware on a real flight controller** while Gazebo simulates the sensors and
physics over USB. It validates the real firmware, timing, and the MAVLink/MAVSDK path on actual
hardware — a stepping stone between pure SITL and real flight.

From Skyforge's point of view, a HITL board is "**hardware that happens to use SITL-like ports**":
you keep Gazebo (so the existing LED visualization still works, or use `stub`), and usually only
the connection flags change. The same `run_commander.py` / `run_skyforge.py` drive it unchanged.

> **Scope note:** HITL is fundamentally **one physical board per drone**. It's a firmware/
> single-vehicle check, *not* a way to rehearse a multi-drone show. For that, see `docs/HARDWARE.md`.

## Steps

1. **Enable HITL on the board** in QGroundControl (Safety → enable HITL), flash the matching PX4
   airframe, and connect the board via USB. Start Gazebo with the HITL-capable airframe so the sim
   feeds simulated sensors to the real autopilot.
2. **Fleet file.** Often just a flags-only file — the board presents a MAVLink endpoint much like a
   SITL instance:

   ```json
   { "use_gcs_beacon": false }
   ```

   If the board's MAVLink endpoint differs from the SITL default (`udpin://0.0.0.0:15000`), give it
   explicitly:

   ```json
   { "use_gcs_beacon": false,
     "drones": [ { "mavlink_url": "serial:///dev/tty.usbmodem1:115200" } ] }
   ```

   - `spawn_local_server: true` (default) if `mavsdk_server` runs on the same host as the board.
   - `spawn_local_server: false` if a `mavsdk_server` is already managed elsewhere; Skyforge then
     only opens the gRPC channel and lets it auto-reconnect.
3. **LEDs.** Keep `gazebo` if you still run the Gazebo GUI for visualization, or
   `export SKYFORGE_LED_BACKEND=stub` for a headless HITL bench.
4. **Launch** exactly as in SITL:

   ```bash
   export SKYFORGE_FLEET=hitl_fleet.json
   ./t6_commander.sh 1        # or  ./t5_skyforge.sh <show>
   ```

5. Confirm the board arms, `_wait_healthy` passes (real EKF GPS + home), and `takeoff`/`land`
   behave — then exercise a formation.

## Verified in software vs. needs the board

Same split as `docs/HARDWARE.md`: the connection plumbing, flag handling, and LED selection are
unit-tested; the actual firmware/timing/arming behavior is what HITL is *for* — confirm it on the
board. The deferred **geodetic** and **safety** gaps (see `docs/HARDWARE.md`) apply here too.
