import asyncio
from mavsdk import System


async def fly_drone(drone_id: int, port: int):
    drone = System(port=50051 + drone_id)
    await drone.connect(system_address=f"udp://:{port}")

    print(f"[Drone {drone_id}] Waiting for connection...")
    async for state in drone.core.connection_state():
        if state.is_connected:
            print(f"[Drone {drone_id}] Connected!")
            break

    print(f"[Drone {drone_id}] Waiting for global position...")
    async for health in drone.telemetry.health():
        if health.is_global_position_ok and health.is_home_position_ok:
            print(f"[Drone {drone_id}] Position OK")
            break

    print(f"[Drone {drone_id}] Arming...")
    await drone.action.arm()
    print(f"[Drone {drone_id}] Taking off...")
    await drone.action.takeoff()
    await asyncio.sleep(5)
    print(f"[Drone {drone_id}] Landing...")
    await drone.action.land()

    async for armed in drone.telemetry.armed():
        if not armed:
            print(f"[Drone {drone_id}] Disarmed.")
            break


async def run():
    await asyncio.gather(
        fly_drone(0, 14540),
        fly_drone(1, 14541),
        fly_drone(2, 14542),
        fly_drone(3, 14543),
    )


if __name__ == "__main__":
    asyncio.run(run())
