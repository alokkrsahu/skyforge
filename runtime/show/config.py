"""Central configuration — all numeric constants."""
import math
import os

# Control loop
CONTROL_HZ = 10
CONTROL_DT  = 1.0 / CONTROL_HZ   # 0.1 s

# Fleet size — override with N_DRONES env var for multi-drone runs
N_DRONES = int(os.environ.get("N_DRONES", "4"))

# MAVSDK ports: one per drone, starting from base values
_MAVLINK_BASE = 14540
_GRPC_BASE    = 50051
MAVLINK_PORTS = [_MAVLINK_BASE + i for i in range(N_DRONES)]
GRPC_PORTS    = [_GRPC_BASE    + i for i in range(N_DRONES)]

# Drone home positions in global NED (North_m, East_m).
# Layout: square grid with 2 m spacing.
#   drone 0: (0,0), drone 1: (0,2), …
_GRID_COLS   = math.ceil(math.sqrt(N_DRONES))
DRONE_HOMES  = [
    (2.0 * (i // _GRID_COLS), 2.0 * (i % _GRID_COLS))
    for i in range(N_DRONES)
]

# Altitude
TAKEOFF_ALT_M  = 5.0
SHOW_ALT_M     = 5.0

# APF collision avoidance — horizontal (NE plane)
APF_D0         = 4.0   # influence radius (m)
APF_K          = 0.8   # repulsion gain
APF_MAX_OFFSET = 2.5   # clamp total NE repulsion offset (m)
# APF — vertical axis
APF_D0_VERT    = 3.0   # vertical influence radius (m)
APF_K_VERT     = 0.4   # vertical repulsion gain
APF_MAX_VERT   = 1.5   # clamp vertical repulsion offset (m)
# APF — emergency hold threshold
APF_MIN_SEP_M  = 1.2   # hard minimum separation; triggers max-strength repulsion

# Barrier convergence (legacy; not used by Skyforge adapter)
BARRIER_THRESHOLD_M = 0.5
