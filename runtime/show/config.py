"""Central configuration — numeric constants and port assignments (single source of truth)."""

# Control loop
CONTROL_HZ = 10
CONTROL_DT  = 1.0 / CONTROL_HZ   # 0.1 s

# ── MAVSDK / PX4 ports — SINGLE SOURCE OF TRUTH ──────────────────────────────
# Per-drone onboard MAVLink link is UDP MAVLINK_BASE+i and MUST match the live
# px4-rc.mavlink in the PX4 build (15000+i). gRPC for mavsdk_server i is GRPC_BASE+i.
# The GCS beacon listens on GCS_BEACON_MAVLINK to satisfy PX4's hard-coded
# remote=14550 "connected to GCS" check (without it PX4 denies arm).
MAVLINK_BASE       = 15000
GRPC_BASE          = 50051
GCS_BEACON_MAVLINK = 14550
GCS_BEACON_GRPC    = 50050

# Altitude
TAKEOFF_ALT_M  = 5.0
SHOW_ALT_M     = 5.0

# Planned inter-drone separation — SINGLE SOURCE OF TRUTH for the runtime. This
# must match the compiler's min_sep_m (validator / deconflict / envelope, default
# 1.5 m). run_skyforge verifies a loaded show's compile_min_sep_m against this at
# startup and warns on mismatch.
MIN_SEP_M      = 1.5

# APF collision avoidance — horizontal (NE plane)
APF_D0         = 4.0   # influence radius (m)
APF_K          = 0.8   # repulsion gain
APF_MAX_OFFSET = 2.5   # clamp total NE repulsion offset (m)
# APF — vertical axis
APF_D0_VERT    = 3.0   # vertical influence radius (m)
APF_K_VERT     = 0.4   # vertical repulsion gain
APF_MAX_VERT   = 1.5   # clamp vertical repulsion offset (m)
# APF — emergency hold threshold. Last-resort max-strength repulsion, deliberately
# BELOW the planned separation: gradual repulsion (APF_D0) handles normal
# approaches; the emergency hold only fires once a drone has already breached the
# planned floor by APF_EMERGENCY_BUFFER_M. Invariant: APF_MIN_SEP_M < MIN_SEP_M.
APF_EMERGENCY_BUFFER_M = 0.3
APF_MIN_SEP_M  = MIN_SEP_M - APF_EMERGENCY_BUFFER_M   # 1.2 m
# Per-drone symmetry-breaking perturbation. BOUNDED so it does NOT grow with fleet
# size (the old `drone_id * 0.01` gave drone 99 a ~1 m permanent bias): a tiny
# N-axis offset of (drone_id % APF_PERTURB_MOD) * APF_PERTURB_STEP_M, zero for
# drone 0, so head-on pairs get slightly different offsets and don't deadlock.
APF_PERTURB_MOD    = 10
APF_PERTURB_STEP_M = 0.01   # max bias = (MOD-1)*STEP = 0.09 m, independent of N
