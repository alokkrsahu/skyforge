"""Controls a single drone. Usage: python3 fly_single.py <drone_id> <mavlink_port>"""
import asyncio
import sys
from mavsdk import System


async def run(drone_id: int, mavlink_port: int):
    grpc_port = 50051 + drone_id
    drone = System(port=grpc_port)
    await drone.connect(system_address=f"udp://:{mavlink_port}")

    print(f"[Drone {drone_id}] Waiting for connection...", flush=True)
    async for state in drone.core.connection_state():
        if state.is_connected:
            print(f"[Drone {drone_id}] Connected!", flush=True)
            break

    print(f"[Drone {drone_id}] Waiting for global position...", flush=True)
    async for health in drone.telemetry.health():
        if health.is_global_position_ok and health.is_home_position_ok:
            print(f"[Drone {drone_id}] Position OK", flush=True)
            break

    print(f"[Drone {drone_id}] Arming...", flush=True)
    await drone.action.arm()
    print(f"[Drone {drone_id}] Taking off...", flush=True)
    await drone.action.takeoff()
    await asyncio.sleep(5)
    print(f"[Drone {drone_id}] Landing...", flush=True)
    await drone.action.land()

    async for armed in drone.telemetry.armed():
        if not armed:
            print(f"[Drone {drone_id}] Disarmed.", flush=True)
            break


if __name__ == "__main__":
    drone_id = int(sys.argv[1])
    mavlink_port = int(sys.argv[2])
    asyncio.run(run(drone_id, mavlink_port))
