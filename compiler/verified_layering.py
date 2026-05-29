"""
Verified altitude layering — the convergent dense-show planner.

The straight-line `band_assignment` predicate (`_min_separation`) misses the
conflicts that the ACTUAL bowed/phase-separated Hermite splines produce, so at
larger fleets it under-bands and the show still collides (the N=16 no-op). And the
old plateau-push deconfliction DIVERGES on dense fields.

`plan()` fixes both by closing the loop on the real trajectories:

  repeat:
    compile the show with the current per-transition band plan
    SAMPLE the actual fitted splines and find every pair that breaches min_sep
      during each transition window  (GAP 1 — detection on the real path)
    accumulate those conflict edges into a per-transition conflict graph
    graph-colour each transition → new band plan

Because the show_builder move is PHASE-SEPARATED (climb straight up at the start
slot, cross horizontally at the band altitude, descend straight down at the target
slot), two drones placed in different bands are >= layer_spacing apart for the whole
horizontal cross and never approach during the vertical legs (slots are >= min_sep
apart). So separating every detected conflicting pair into different bands removes
that conflict without creating a horizontal one.

Convergence (GAP 2): the accumulated conflict graph only GROWS and is bounded by
n(n-1)/2 edges per transition; each round either discovers a new edge (graph grows)
or discovers none (fixed point → stop). The colouring is a deterministic function
of the graph, so the band plan converges. We return the lowest-conflict show seen.
If the chromatic number exceeds the altitude budget the residual is reported
honestly (resolved=False) and the pipeline fast-fails — never an unsafe show.
"""
from __future__ import annotations

import numpy as np

from compiler.sampling import sample_positions


def _colour(n: int, edges: set[tuple[int, int]]) -> list[int]:
    """Greedy graph colouring (descending degree) → band index per drone."""
    adj: list[set[int]] = [set() for _ in range(n)]
    for i, j in edges:
        adj[i].add(j)
        adj[j].add(i)
    band = [-1] * n
    for i in sorted(range(n), key=lambda x: -len(adj[x])):
        used = {band[k] for k in adj[i] if band[k] >= 0}
        b = 0
        while b in used:
            b += 1
        band[i] = b
    return band


def _detect(show, windows, min_sep_m, sample_hz):
    """
    Per transition window, the set of (i<j) pairs whose 3-D distance drops below
    min_sep_m on the fitted splines, plus the total count of breaching samples (Φ).
    """
    trajs = show.trajectories
    n = len(trajs)
    edges_per: list[set[tuple[int, int]]] = []
    phi = 0
    dt = 1.0 / sample_hz
    for (t0, t1) in windows:
        ts = np.arange(t0, t1 + dt * 0.5, dt)
        edges: set[tuple[int, int]] = set()
        if len(ts) == 0:
            edges_per.append(edges)
            continue
        pos = sample_positions(trajs, ts)               # (n, T, 3)
        for i in range(n - 1):
            d = np.linalg.norm(pos[i + 1:] - pos[i][None, :, :], axis=2)   # (n-i-1, T)
            breach = d < min_sep_m
            jrows = np.where(breach.any(axis=1))[0]
            for jr in jrows:
                edges.add((i, i + 1 + int(jr)))
            phi += int(breach.sum())
        edges_per.append(edges)
    return edges_per, phi


def plan(builder, min_sep_m: float, max_rounds: int = 16, sample_hz: float = 40.0):
    """
    Compile *builder* into the lowest-conflict ShowFile reachable by verified
    altitude layering. Returns (show, residual_phi). residual_phi == 0 means
    collision-free at sample_hz; > 0 means the planner could not fully resolve
    (the pipeline then fails fast — never ships it).
    """
    windows = builder.transition_windows()
    nt = len(windows)
    n  = builder._n

    acc: list[set[tuple[int, int]]] = [set() for _ in range(nt)]
    band_plan: list | None = None                       # None everywhere = default compile

    best_show = builder.compile(band_plan=band_plan)
    edges, best_phi = _detect(best_show, windows, min_sep_m, sample_hz)
    if best_phi == 0:
        return best_show, 0

    for _ in range(max_rounds):
        # Grow the conflict graph with whatever the latest splines revealed.
        grew = False
        for t in range(nt):
            if edges[t] - acc[t]:
                acc[t] |= edges[t]
                grew = True
        if not grew:
            break                                       # fixed point — colouring won't change

        band_plan = [(_colour(n, acc[t]) if acc[t] else None) for t in range(nt)]
        show = builder.compile(band_plan=band_plan)
        edges, phi = _detect(show, windows, min_sep_m, sample_hz)
        if phi < best_phi:
            best_show, best_phi = show, phi
        if best_phi == 0:
            return best_show, 0

    return best_show, best_phi
