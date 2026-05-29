"""Artificial Potential Field repulsion — 3D, velocity-aware, with emergency hold."""
import math
from typing import List, Tuple

from .config import (
    APF_D0, APF_K, APF_MAX_OFFSET,
    APF_D0_VERT, APF_K_VERT, APF_MAX_VERT,
    APF_MIN_SEP_M,
)


def compute_apf_offset(
    own_ned:    Tuple[float, float, float],
    own_vel:    Tuple[float, float, float],
    others_ned: List[Tuple[float, float, float]],
    drone_id:   int,
    d0:         float = APF_D0,
    k:          float = APF_K,
) -> Tuple[float, float, float]:
    """
    Return (dN, dE, dD) repulsion offset to add to the nominal position setpoint.

    - 3D: separate horizontal (NE) and vertical (D) repulsion channels.
    - Velocity-aware: repulsion only fires when drones are approaching each other
      (closing speed > 0), preventing jitter when they are moving apart.
      Closing speed scales magnitude — faster approach = stronger push.
    - Emergency hold: if any neighbour is inside APF_MIN_SEP_M, maximum-strength
      repulsion is returned immediately regardless of velocity.
    - Asymmetric perturbation (drone_id * 0.05) breaks head-on deadlocks.
    """
    own_n, own_e, own_d = own_ned
    vel_n, vel_e, vel_d = own_vel

    ne_dN   = drone_id * 0.01   # tiny asymmetric perturbation — breaks head-on symmetry
    ne_dE   = 0.0
    vert_dD = 0.0

    for other in others_ned:
        oth_n, oth_e, oth_d = other

        # ── Horizontal (NE) repulsion ─────────────────────────────────────────
        dN   = own_n - oth_n
        dE   = own_e - oth_e
        dD_3 = own_d - oth_d
        d_ne = math.hypot(dN, dE)
        d_3d = math.sqrt(d_ne * d_ne + dD_3 * dD_3)

        if d_3d < APF_MIN_SEP_M:
            # Emergency hold — 3D proximity; return max repulsion, bypass velocity check
            if d_ne > 1e-6:
                s = APF_MAX_OFFSET / d_ne
                return (dN * s, dE * s, vert_dD)
            # Drones nearly collocated in NE — push vertically instead
            sign = 1.0 if dD_3 >= 0.0 else -1.0
            return (0.0, 0.0, sign * APF_MAX_VERT)

        if 1e-3 < d_ne < d0:
            unit_n  = dN / d_ne
            unit_e  = dE / d_ne
            v_close = -(vel_n * unit_n + vel_e * unit_e)   # positive = approaching
            if v_close > 0.0:
                speed_scale = 1.0 + 0.3 * v_close
                mag  = k * (1.0 / d_ne - 1.0 / d0) / (d_ne * d_ne) * speed_scale
                ne_dN += mag * unit_n
                ne_dE += mag * unit_e

        # ── Vertical (D) repulsion ────────────────────────────────────────────
        dD    = own_d - oth_d
        d_abs = abs(dD)
        if 1e-3 < d_abs < APF_D0_VERT:
            unit_d    = dD / d_abs
            v_close_d = -(vel_d * unit_d)
            if v_close_d > 0.0:
                speed_scale = 1.0 + 0.3 * v_close_d
                mag = APF_K_VERT * (1.0 / d_abs - 1.0 / APF_D0_VERT) / (d_abs * d_abs) * speed_scale
                vert_dD += mag * unit_d

    # Clamp NE and vertical offsets independently
    ne_total = math.hypot(ne_dN, ne_dE)
    if ne_total > APF_MAX_OFFSET:
        s      = APF_MAX_OFFSET / ne_total
        ne_dN *= s
        ne_dE *= s

    if abs(vert_dD) > APF_MAX_VERT:
        vert_dD = math.copysign(APF_MAX_VERT, vert_dD)

    return (ne_dN, ne_dE, vert_dD)
