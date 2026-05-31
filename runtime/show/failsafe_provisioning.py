"""
PX4 failsafe + geofence provisioning.

Skyforge ships NO on-vehicle safety of its own (see docs/HARDWARE.md "Gaps") — the
geofence / RTL / battery / RC-loss / offboard-loss failsafes live on the PX4 flight
controller and have to be set per vehicle. This module pushes a sensible failsafe
parameter set to each drone over MAVSDK's param plugin, so the fleet's autonomous
safety net is configured from one place before arming.

Opt-in: nothing runs unless `$SKYFORGE_FAILSAFE_CONFIG` (a JSON file) is set or a caller
invokes `provision_failsafes()` explicitly. Defaults are SITL-sane.

DEFERRED (hardware): the exact param names/semantics vary slightly across PX4 releases —
confirm on the real Pixhawk in HITL (the values here target current PX4 stable). The set
is intentionally conservative (Hold/RTL/Land actions, never Terminate).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass


@dataclass
class FailsafeConfig:
    geofence_radius_m:   float = 100.0   # GF_MAX_HOR_DIST
    geofence_alt_max_m:  float = 50.0    # GF_MAX_VER_DIST
    geofence_action:     int   = 2       # GF_ACTION: 1=Warn 2=Hold 3=RTL 5=Land
    rtl_return_alt_m:    float = 30.0    # RTL_RETURN_ALT
    battery_low_frac:    float = 0.20    # BAT_LOW_THR  (fraction 0-1)
    battery_crit_frac:   float = 0.10    # BAT_CRIT_THR
    low_bat_action:      int   = 2       # COM_LOW_BAT_ACT: 2=Return
    offboard_loss_action: int  = 1       # COM_OBL_ACT: 0=disabled 1=Land/Hold (version-dependent)
    rc_in_mode:          int   = 1       # COM_RC_IN_MODE: 0=RC 1=Joystick/no-RC 2=both (SITL→1)
    rcl_except_offboard: bool  = True    # COM_RCL_EXCEPT bit 2 → don't trip RC-loss in offboard

    @classmethod
    def from_dict(cls, d: dict) -> "FailsafeConfig":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    @classmethod
    def from_env(cls) -> "FailsafeConfig | None":
        path = os.environ.get("SKYFORGE_FAILSAFE_CONFIG", "").strip()
        if not path:
            return None
        with open(path) as f:
            return cls.from_dict(json.load(f))

    def to_params(self) -> list[tuple[str, str, float]]:
        """(name, kind, value) tuples; kind is 'int' or 'float' → the right param setter."""
        return [
            ("GF_ACTION",       "int",   self.geofence_action),
            ("GF_MAX_HOR_DIST", "float", self.geofence_radius_m),
            ("GF_MAX_VER_DIST", "float", self.geofence_alt_max_m),
            ("RTL_RETURN_ALT",  "float", self.rtl_return_alt_m),
            ("BAT_LOW_THR",     "float", self.battery_low_frac),
            ("BAT_CRIT_THR",    "float", self.battery_crit_frac),
            ("COM_LOW_BAT_ACT", "int",   self.low_bat_action),
            ("COM_OBL_ACT",     "int",   self.offboard_loss_action),
            ("COM_RC_IN_MODE",  "int",   self.rc_in_mode),
            ("COM_RCL_EXCEPT",  "int",   4 if self.rcl_except_offboard else 0),
        ]


async def provision_failsafes(drone, cfg: FailsafeConfig | None = None,
                              verbose: bool = False) -> list[str]:
    """Push the failsafe param set to one drone via the MAVSDK param plugin.
    Returns the list of param names successfully applied. Per-param failures are
    logged and skipped (a missing param on an older firmware shouldn't abort the rest)."""
    cfg = cfg or FailsafeConfig()
    applied: list[str] = []
    for name, kind, value in cfg.to_params():
        try:
            if kind == "int":
                await drone.param.set_param_int(name, int(value))
            else:
                await drone.param.set_param_float(name, float(value))
            applied.append(name)
            if verbose:
                print(f"[failsafe] {name} = {value}")
        except Exception as exc:   # param absent on this firmware / transient
            print(f"[failsafe] skip {name}: {exc}")
    return applied
