"""
Pluggable LED backend — lets the SAME runtime drive LEDs in Gazebo (SITL) or, on
real hardware, through a driver that replaces the Gazebo calls.

The two Gazebo backends below are the EXACT, visually-verified SITL implementations
moved verbatim out of ``skyforge_adapter`` (emissive motor-base meshes via
``visual_config``) and ``commander/dynamic_adapter`` (arm-tip POINT lights via
``light_config``) — only their home changed; the gz protos/args are byte-for-byte
identical, so SITL behavior is unchanged.

Selected by ``$SKYFORGE_LED_BACKEND`` (default ``gazebo``):
  * ``gazebo`` — player → :class:`GazeboVisualLed`, commander → :class:`GazeboPointLightLed`
  * ``stub``   — :class:`StubLed` (no Gazebo subprocesses; the seam where a real LED
    driver — MAVLink / DroneCAN / companion-computer GPIO/serial — plugs in)

IMPORTANT: construct ONE backend instance per runtime mode (a module-level singleton
at the call site) so the concurrency semaphore is shared fleet-wide. A per-drone
backend would give each drone its own 16-permit semaphore (~N×16 concurrent ``gz``
processes), starving the event loop and dropping the offboard setpoint stream.
"""
import asyncio
import os

LED_BACKEND_ENV = "SKYFORGE_LED_BACKEND"


class LedBackend:
    """Interface: set drone ``drone_id``'s LED to colour (r, g, b) in [0, 1]."""
    async def set_led(self, drone_id: int, r: float, g: float, b: float) -> None:
        raise NotImplementedError


class _GazeboLed(LedBackend):
    """Shared Gazebo plumbing: the GZ env snapshot + a lazy, fleet-wide semaphore.

    The semaphore is created on the running loop (import never touches an event
    loop). One backend instance per mode → one semaphore shared across all drones.
    """
    _GZ_MAX_CONCURRENT = 16

    def __init__(self) -> None:
        self._sem: "asyncio.Semaphore | None" = None
        # gz transport needs GZ_IP set to reach the sim's partition; without it the
        # service call silently times out (no LED change).
        self._env = {**os.environ, "GZ_IP": "127.0.0.1"}

    def _gz_sem(self) -> "asyncio.Semaphore":
        if self._sem is None:
            self._sem = asyncio.Semaphore(self._GZ_MAX_CONCURRENT)
        return self._sem


class GazeboVisualLed(_GazeboLed):
    """SITL player: recolor the emissive motor-base meshes via visual_config."""
    _SVC     = "/world/default/visual_config"
    _VISUALS = ["5010_motor_base_0", "5010_motor_base_1",
                "5010_motor_base_2", "5010_motor_base_3"]

    async def set_led(self, drone_id: int, r: float, g: float, b: float) -> None:
        model = f"x500_{drone_id}"
        mat   = (
            f"material {{"
            f"ambient {{r:{r:.3f} g:{g:.3f} b:{b:.3f} a:1}} "
            f"diffuse {{r:{r:.3f} g:{g:.3f} b:{b:.3f} a:1}} "
            f"emissive {{r:{r:.3f} g:{g:.3f} b:{b:.3f} a:1}}"
            f"}}"
        )
        sem = self._gz_sem()

        async def _send(vis: str) -> None:
            proto = f'name: "{model}::base_link::{vis}" {mat}'
            async with sem:
                proc  = await asyncio.create_subprocess_exec(
                    "gz", "service", "-s", self._SVC,
                    "--reqtype", "gz.msgs.Visual",
                    "--reptype", "gz.msgs.Boolean",
                    "--timeout", "200",
                    "--req", proto,
                    env=self._env,
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                )
                await proc.wait()

        await asyncio.gather(*(_send(v) for v in self._VISUALS))


class GazeboPointLightLed(_GazeboLed):
    """Commander: recolor the four arm-tip POINT lights via light_config.

    Each light must be re-sent with its exact link-relative pose + attenuation,
    since light_config replaces the whole light (model.sdf: pose x y 0.05,
    attenuation range 5 / 0.3 / 0.2 / 0.01).
    """
    _SVC    = "/world/default/light_config"
    _LIGHTS = {
        "light_front_left":  (0.174, 0.174, 0.05),
        "light_front_right": (0.174, -0.174, 0.05),
        "light_rear_left":  (-0.174, 0.174, 0.05),
        "light_rear_right": (-0.174, -0.174, 0.05),
    }

    async def set_led(self, drone_id: int, r: float, g: float, b: float) -> None:
        model = f"x500_{drone_id}"
        sem   = self._gz_sem()

        async def _send(light: str, pos: tuple) -> None:
            x, y, z = pos
            req = (
                f'name: "{model}::base_link::{light}" type: POINT '
                f'diffuse {{r:{r:.3f} g:{g:.3f} b:{b:.3f} a:1}} '
                f'specular {{r:{r * 0.3:.3f} g:{g * 0.3:.3f} b:{b * 0.3:.3f} a:1}} '
                f'pose {{position {{x:{x} y:{y} z:{z}}}}} '
                f'range: 5.0 attenuation_constant: 0.3 attenuation_linear: 0.2 '
                f'attenuation_quadratic: 0.01 cast_shadows: false intensity: 1.0'
            )
            async with sem:
                proc = await asyncio.create_subprocess_exec(
                    "gz", "service", "-s", self._SVC,
                    "--reqtype", "gz.msgs.Light",
                    "--reptype", "gz.msgs.Boolean",
                    "--timeout", "300",
                    "--req", req,
                    env=self._env,
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                )
                await proc.wait()

        await asyncio.gather(*(_send(l, p) for l, p in self._LIGHTS.items()))


class StubLed(LedBackend):
    """Hardware placeholder: no Gazebo, no subprocesses.

    This is the seam where a real LED driver (MAVLink / DroneCAN / companion-computer
    GPIO/serial) plugs in. Logs once so it's obvious the stub is active, then stays
    silent (the flight loop must never block on LED I/O).
    """
    def __init__(self) -> None:
        self._warned = False

    async def set_led(self, drone_id: int, r: float, g: float, b: float) -> None:
        if not self._warned:
            self._warned = True
            print(f"[led] {LED_BACKEND_ENV}=stub — LED commands are no-ops (no hardware "
                  f"LED driver wired). See docs/HARDWARE.md.")
        return None


def make_led_backend(mode: str) -> LedBackend:
    """Return the LED backend for a runtime mode ("player" | "commander").

    Selected by ``$SKYFORGE_LED_BACKEND`` (default "gazebo"). An unknown value
    warns and falls back to "gazebo" — never crash a show over an LED setting.
    """
    choice = (os.environ.get(LED_BACKEND_ENV) or "gazebo").strip().lower()
    if choice == "stub":
        return StubLed()
    if choice not in ("gazebo", ""):
        print(f"[led] unknown {LED_BACKEND_ENV}={choice!r}; using 'gazebo'.")
    if mode == "commander":
        return GazeboPointLightLed()
    return GazeboVisualLed()   # "player" (default)
