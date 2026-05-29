"""
Trajectory deconfliction  (Phase 3).

Takes a ShowFile with compiled nominal trajectories and resolves horizontal
separation violations by injecting corrective waypoints and re-fitting the
affected polynomial segments.  Only NominalTrajectory objects are modified;
LED tracks, reactive bindings, and metadata are untouched.

Algorithm (one pass):
  1. Sample all trajectories at sample_hz using 3-D distance (matches validator).
  2. For each pair (i, j) with separation < min_sep_m, cluster the bad samples
     into conflict windows.
  3. For each window:
       a. Find the time of minimum separation (t_closest) and its push direction.
       b. Apply that SAME push direction and magnitude at every correction knot
          through the window (spaced by correction_spacing_s).  Using a constant
          direction creates a "plateau" correction — the spline interpolates a
          flat lateral offset across the entire window without oscillating.
       c. Add zero-correction "pad" knots just before and after the window so
          the spline ramps in and out smoothly.
  4. Re-fit a natural cubic spline for any drone that received corrections.
  Repeat up to max_iters until no conflicts remain or the cap is hit.
"""
from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass

import numpy as np

from compiler.sampling import sample_positions
from compiler.trajectory_generator import fit_trajectory
from core.show_format.schema import NominalTrajectory, ShowFile, Vec3


# ── Config & result ───────────────────────────────────────────────────────────

@dataclass
class DeconflictConfig:
    min_sep_m:            float = 1.5   # minimum 3-D separation to enforce (m)
    sample_hz:            float = 20.0  # conflict-detection sampling rate (Hz)
    correction_spacing_s: float = 2.0   # spacing between correction knots (s)
    margin_m:             float = 0.3   # clearance added on top of min_sep_m
    max_deflection_m:     float = 3.0   # cap on total lateral correction per knot
    window_pad_s:         float = 1.0   # pad knots added before/after conflict window
    cluster_gap_s:        float = 1.0   # merge conflict windows closer than this (s)
    max_iters:            int   = 20    # iteration limit (CompilePipeline scales this up for large fleets)


@dataclass
class DeconflictResult:
    show:            ShowFile
    iters_run:       int
    conflicts_found: int   # total conflict windows across all iterations
    resolved:        bool  # True if no violations remain after deconfliction


# ── Internal helpers ──────────────────────────────────────────────────────────

def _cluster_times(
    times: list[float],
    gap_s: float,
) -> list[tuple[float, float]]:
    """Group consecutive timestamps into windows separated by more than gap_s."""
    if not times:
        return []
    windows: list[tuple[float, float]] = []
    start = prev = times[0]
    for t in times[1:]:
        if t - prev > gap_s:
            windows.append((start, prev))
            start = t
        prev = t
    windows.append((start, prev))
    return windows


def _knot_times_of(traj: NominalTrajectory) -> list[float]:
    """Return the segment-boundary timestamps from a compiled trajectory."""
    times = [seg.t_start for seg in traj.segments]
    times.append(traj.segments[-1].t_end)
    return times


def _traj_velocity(traj: NominalTrajectory, t: float) -> Vec3:
    """Evaluate first derivative at time t."""
    for seg in traj.segments:
        if seg.t_start <= t <= seg.t_end:
            return seg.evaluate_velocity(t)
    if t < traj.segments[0].t_start:
        return traj.segments[0].evaluate_velocity(traj.segments[0].t_start)
    return traj.segments[-1].evaluate_velocity(traj.segments[-1].t_end)


def _clamp_correction(c: Vec3, max_m: float) -> Vec3:
    """Clamp lateral correction magnitude without changing direction."""
    mag = math.sqrt(c.n * c.n + c.e * c.e)
    if mag <= max_m:
        return c
    scale = max_m / mag
    return Vec3(c.n * scale, c.e * scale, c.d)


def _push_direction(
    trajs: list[NominalTrajectory],
    i: int,
    j: int,
    t: float,
    n: int,
) -> tuple[float, float]:
    """
    Unit vector pointing from drone j toward drone i at time t.
    Falls back to the perpendicular of the relative velocity when coincident.
    """
    pi = trajs[i].evaluate(t)
    pj = trajs[j].evaluate(t)
    dx = pi.n - pj.n
    dy = pi.e - pj.e
    dist_h = math.sqrt(dx * dx + dy * dy)

    if dist_h > 1e-6:
        return dx / dist_h, dy / dist_h

    # Coincident in the horizontal plane — perpendicular to relative velocity
    vi = _traj_velocity(trajs[i], t)
    vj = _traj_velocity(trajs[j], t)
    rvn = vi.n - vj.n
    rve = vi.e - vj.e
    rvmag = math.sqrt(rvn * rvn + rve * rve)
    if rvmag > 1e-6:
        return -rve / rvmag, rvn / rvmag   # 90° CCW of relative velocity

    # Last resort: fixed angle from drone index
    angle = i * (2.0 * math.pi / max(n, 2))
    return math.cos(angle), math.sin(angle)


# ── Single deconfliction pass ─────────────────────────────────────────────────

def _deconflict_pass(
    show: ShowFile,
    cfg:  DeconflictConfig,
) -> tuple[ShowFile, int]:
    """
    One correction pass.

    Returns (updated_show, n_conflict_windows).
    If n_conflict_windows == 0 the show is already clear and the show object
    returned is the same (no re-fitting).
    """
    n        = len(show.trajectories)
    dt       = 1.0 / cfg.sample_hz
    duration = show.metadata.duration_s
    n_samp   = int(duration / dt) + 1
    sample_t    = [k * dt for k in range(n_samp)]
    sample_t_np = np.asarray(sample_t)

    # Sample all trajectories once (vectorised) → (n, n_samp, 3)
    sampled = sample_positions(show.trajectories, sample_t_np)

    # corrections[drone_id][t] = Vec3 additive lateral offset at knot time t
    corrections: dict[int, dict[float, Vec3]] = {i: {} for i in range(n)}
    extra_knots: dict[int, set[float]]        = {i: set() for i in range(n)}
    n_windows = 0

    for i in range(n):
        for j in range(i + 1, n):
            d_ij     = np.linalg.norm(sampled[i] - sampled[j], axis=1)   # (n_samp,)
            bad_mask = d_ij < cfg.min_sep_m
            if not bad_mask.any():
                continue
            bad_times = sample_t_np[bad_mask].tolist()

            for t_w0, t_w1 in _cluster_times(bad_times, cfg.cluster_gap_s):
                n_windows += 1

                # Find tightest point → direction and magnitude for this window
                win       = (sample_t_np >= t_w0) & (sample_t_np <= t_w1)
                d_win     = np.where(win, d_ij, np.inf)
                kmin      = int(np.argmin(d_win))
                min_dist  = float(d_win[kmin])
                t_closest = float(sample_t_np[kmin])

                nx, ny = _push_direction(show.trajectories, i, j, t_closest, n)
                push   = min(
                    (cfg.min_sep_m + cfg.margin_m - min_dist) / 2.0,
                    cfg.max_deflection_m / 2.0,
                )

                t_before = max(0.0,     t_w0 - cfg.window_pad_s)
                t_after  = min(duration, t_w1 + cfg.window_pad_s)

                # Pad knots at window boundaries (smooth ramp, no correction)
                for drone_id in (i, j):
                    extra_knots[drone_id].update({
                        round(t_before, 9), round(t_after, 9),
                    })

                # Plateau: constant push at every correction knot within the window.
                # Using the same (nx, ny, push) at all knots prevents direction changes
                # that would cause spline oscillation.
                plateau_knots: set[float] = set()

                t_corr = t_w0
                while t_corr <= t_w1 + 1e-9:
                    plateau_knots.add(round(t_corr, 9))
                    t_corr += cfg.correction_spacing_s
                plateau_knots.add(round(t_w1,     9))   # ensure window end
                plateau_knots.add(round(t_closest, 9))  # ensure tightest point

                for t_key in plateau_knots:
                    for drone_id in (i, j):
                        extra_knots[drone_id].add(t_key)
                    for drone_id, sign in ((i, 1.0), (j, -1.0)):
                        prev = corrections[drone_id].get(t_key, Vec3())
                        raw  = Vec3(
                            prev.n + sign * nx * push,
                            prev.e + sign * ny * push,
                            prev.d,
                        )
                        corrections[drone_id][t_key] = _clamp_correction(
                            raw, cfg.max_deflection_m
                        )

    if n_windows == 0:
        return show, 0

    # Re-fit trajectories for drones that received corrections
    new_trajectories: list[NominalTrajectory] = []
    for traj in show.trajectories:
        i = traj.drone_id
        if not extra_knots[i] and not corrections[i]:
            new_trajectories.append(traj)
            continue

        orig_times = _knot_times_of(traj)
        all_times  = sorted(set(orig_times) | extra_knots[i])

        knot_positions = []
        for t in all_times:
            p = traj.evaluate(t)
            c = corrections[i].get(t, Vec3())
            knot_positions.append(Vec3(p.n + c.n, p.e + c.e, p.d))

        new_traj = fit_trajectory(all_times, knot_positions)
        new_traj = dataclasses.replace(new_traj, drone_id=i)
        new_trajectories.append(new_traj)

    return dataclasses.replace(show, trajectories=new_trajectories), n_windows


# ── Public entry point ────────────────────────────────────────────────────────

def deconflict(
    show:   ShowFile,
    config: DeconflictConfig | None = None,
) -> DeconflictResult:
    """
    Iteratively deconflict nominal trajectories until clear or max_iters reached.

    Returns a DeconflictResult whose .show field is safe to pass to the next
    pipeline stage (envelope computation).
    """
    cfg        = config or DeconflictConfig()
    current    = show
    total_wnd  = 0
    prev_found = None
    rising     = 0
    iters_run  = 0

    for iteration in range(cfg.max_iters):
        current, n_found = _deconflict_pass(current, cfg)
        total_wnd += n_found
        iters_run  = iteration + 1
        if n_found == 0:
            # iters_run here = number of CORRECTIVE passes (passes 0..iteration-1
            # made corrections; this pass found none). 0 for an already-clean show.
            return DeconflictResult(
                show=current,
                iters_run=iteration,
                conflicts_found=total_wnd,
                resolved=True,
            )
        # Divergence guard: in dense, single-altitude fields the plateau-push can
        # oscillate and make things *worse* (conflict windows rise pass-over-pass).
        # Bail after 2 consecutive increases instead of burning the whole budget —
        # the residual check below then reports resolved=False quickly.
        if prev_found is not None and n_found > prev_found:
            rising += 1
            if rising >= 2:
                print(
                    f"[deconflict] diverging (windows {prev_found}->{n_found}); "
                    f"stopping after {iters_run} iters — not resolvable by lateral correction."
                )
                break
        else:
            rising = 0
        prev_found = n_found

    # Iteration cap or divergence — verify residual state (vectorised)
    dt       = 1.0 / cfg.sample_hz
    duration = current.metadata.duration_s
    n        = len(current.trajectories)
    times    = np.arange(0.0, duration + dt * 0.5, dt)
    pos      = sample_positions(current.trajectories, times)   # (n, T, 3)
    resolved = True
    for i in range(n):
        for j in range(i + 1, n):
            if bool((np.linalg.norm(pos[i] - pos[j], axis=1) < cfg.min_sep_m).any()):
                resolved = False
                break
        if not resolved:
            break

    return DeconflictResult(
        show=current,
        iters_run=iters_run,
        conflicts_found=total_wnd,
        resolved=resolved,
    )
