"""
Formation generators for the Skyforge compiler.

Each public generator takes n (fleet size) and returns a list of exactly n
(dN, dE) offset tuples centred on (0, 0) in the NED horizontal plane.

Built-in names for ShowBuilder.add_act():
    "circle"         equally-spaced ring
    "grid"           rectangular grid (as square as possible)
    "line"           E-W horizontal line
    "v_shape" / "v"  V pointing north
    "star"           star polygon (5-pointed by default)
    "spiral"         Archimedean spiral from centre outward
    "diamond"        legacy 4-point diamond (padded/subsampled for n ≠ 4)
    "arrow"          legacy 4-drone arrowhead (padded/subsampled for n ≠ 4)

Sky-art text spec:
    "text:HELLO"            — 5×7 pixel font, 2 m/pixel spacing
    "text:HELLO:scale=3.0"  — override pixel spacing to 3 m

Use pixel_count(string) to find out how many drones perfectly fill the text
(no padding, no subsampling).  With more drones the extras orbit the text in a
ring; with fewer, pixels are sub-sampled evenly.

Custom positions can also be passed as a list of (dN, dE) tuples directly to
ShowBuilder.add_act() — they are padded/subsampled to the fleet size automatically.
"""
from __future__ import annotations

import math


# ── Internal helpers ──────────────────────────────────────────────────────────

def _centre(pts: list[tuple[float, float]]) -> list[tuple[float, float]]:
    if not pts:
        return pts
    cn = sum(p[0] for p in pts) / len(pts)
    ce = sum(p[1] for p in pts) / len(pts)
    return [(p[0] - cn, p[1] - ce) for p in pts]


def _pad_to(pts: list[tuple[float, float]], n: int) -> list[tuple[float, float]]:
    """
    Return exactly n positions.
    - Surplus (m > n): subsample indices evenly so shape stays representative.
    - Deficit (m < n): append extra drones on a ring just outside the formation.
    """
    m = len(pts)
    if m == n:
        return list(pts)
    if m > n:
        if n == 1:
            return [pts[0]]
        indices = [round(i * (m - 1) / (n - 1)) for i in range(n)]
        return [pts[i] for i in indices]
    r_ring = (max(math.hypot(p[0], p[1]) for p in pts) if pts else 0.0) + 3.0
    extra = n - m
    ring = [
        (r_ring * math.cos(math.pi / 2 - k * 2 * math.pi / extra),
         r_ring * math.sin(math.pi / 2 - k * 2 * math.pi / extra))
        for k in range(extra)
    ]
    return list(pts) + ring


# ── Formation generators ──────────────────────────────────────────────────────

def circle(n: int, radius_m: float = 5.0) -> list[tuple[float, float]]:
    """N drones equally spaced on a circle, first drone at due north."""
    return [
        (radius_m * math.cos(math.pi / 2 - k * 2 * math.pi / n),
         radius_m * math.sin(math.pi / 2 - k * 2 * math.pi / n))
        for k in range(n)
    ]


def grid(n: int, cols: int | None = None, spacing_m: float = 2.0) -> list[tuple[float, float]]:
    """Rectangular grid, as square as possible when cols is not given."""
    if cols is None:
        cols = math.ceil(math.sqrt(n))
    rows = math.ceil(n / cols)
    pts = [
        ((r - (rows - 1) / 2.0) * spacing_m, (c - (cols - 1) / 2.0) * spacing_m)
        for r in range(rows)
        for c in range(cols)
    ]
    return pts[:n]


def line(n: int, spacing_m: float = 2.0) -> list[tuple[float, float]]:
    """N drones in an E-W line, centred on origin."""
    return [(0.0, (k - (n - 1) / 2.0) * spacing_m) for k in range(n)]


def v_shape(
    n: int,
    spacing_m: float = 2.0,
    half_angle_deg: float = 35.0,
) -> list[tuple[float, float]]:
    """V-shape pointing north; tip drone at origin for odd n."""
    a    = math.radians(half_angle_deg)
    half = n // 2
    tip  = [(0.0, 0.0)] if n % 2 == 1 else []
    left  = [(-k * spacing_m * math.sin(a),  k * spacing_m * math.cos(a)) for k in range(1, half + 1)]
    right = [(-k * spacing_m * math.sin(a), -k * spacing_m * math.cos(a)) for k in range(1, half + 1)]
    return _centre(tip + left + right)


def star(
    n: int,
    n_points: int = 5,
    r_outer: float = 6.0,
    r_inner: float = 3.0,
) -> list[tuple[float, float]]:
    """Drones on a star polygon with n_points arms, padded/subsampled to n."""
    verts: list[tuple[float, float]] = []
    for k in range(2 * n_points):
        r     = r_outer if k % 2 == 0 else r_inner
        angle = math.pi / 2 - k * math.pi / n_points
        verts.append((r * math.cos(angle), r * math.sin(angle)))
    return _pad_to(verts, n)


def spiral(
    n: int,
    turns: float = 2.0,
    r_max: float = 8.0,
) -> list[tuple[float, float]]:
    """Archimedean spiral from centre outward."""
    if n == 1:
        return [(0.0, 0.0)]
    return [
        (r_max * (k / (n - 1)) * math.cos(math.pi / 2 + 2 * math.pi * turns * k / (n - 1)),
         r_max * (k / (n - 1)) * math.sin(math.pi / 2 + 2 * math.pi * turns * k / (n - 1)))
        for k in range(n)
    ]


# ── Sky art: 5×7 bitmap pixel font ───────────────────────────────────────────
# Rows are ordered top→bottom; '#' = lit pixel, ' ' = off.

_FONT_5x7: dict[str, list[str]] = {
    'A': [" ### ", "#   #", "#   #", "#####", "#   #", "#   #", "#   #"],
    'B': ["#### ", "#   #", "#   #", "#### ", "#   #", "#   #", "#### "],
    'C': [" ####", "#    ", "#    ", "#    ", "#    ", "#    ", " ####"],
    'D': ["#### ", "#   #", "#   #", "#   #", "#   #", "#   #", "#### "],
    'E': ["#####", "#    ", "#    ", "#### ", "#    ", "#    ", "#####"],
    'F': ["#####", "#    ", "#    ", "#### ", "#    ", "#    ", "#    "],
    'G': [" ####", "#    ", "#    ", "# ###", "#   #", "#   #", " ####"],
    'H': ["#   #", "#   #", "#   #", "#####", "#   #", "#   #", "#   #"],
    'I': [" ### ", "  #  ", "  #  ", "  #  ", "  #  ", "  #  ", " ### "],
    'J': ["  ###", "   # ", "   # ", "   # ", "#  # ", "#  # ", " ##  "],
    'K': ["#   #", "#  # ", "# #  ", "##   ", "# #  ", "#  # ", "#   #"],
    'L': ["#    ", "#    ", "#    ", "#    ", "#    ", "#    ", "#####"],
    'M': ["#   #", "## ##", "# # #", "#   #", "#   #", "#   #", "#   #"],
    'N': ["#   #", "##  #", "# # #", "#  ##", "#   #", "#   #", "#   #"],
    'O': [" ### ", "#   #", "#   #", "#   #", "#   #", "#   #", " ### "],
    'P': ["#### ", "#   #", "#   #", "#### ", "#    ", "#    ", "#    "],
    'Q': [" ### ", "#   #", "#   #", "#   #", "# # #", "#  ##", " ## #"],
    'R': ["#### ", "#   #", "#   #", "#### ", "# #  ", "#  # ", "#   #"],
    'S': [" ####", "#    ", "#    ", " ### ", "    #", "    #", "#### "],
    'T': ["#####", "  #  ", "  #  ", "  #  ", "  #  ", "  #  ", "  #  "],
    'U': ["#   #", "#   #", "#   #", "#   #", "#   #", "#   #", " ### "],
    'V': ["#   #", "#   #", "#   #", "#   #", " # # ", " # # ", "  #  "],
    'W': ["#   #", "#   #", "#   #", "# # #", "# # #", "## ##", "#   #"],
    'X': ["#   #", "#   #", " # # ", "  #  ", " # # ", "#   #", "#   #"],
    'Y': ["#   #", "#   #", " # # ", "  #  ", "  #  ", "  #  ", "  #  "],
    'Z': ["#####", "    #", "   # ", "  #  ", " #   ", "#    ", "#####"],
    '0': [" ### ", "#  ##", "# # #", "## # ", "#   #", "#   #", " ### "],
    '1': ["  #  ", " ##  ", "  #  ", "  #  ", "  #  ", "  #  ", " ### "],
    '2': [" ### ", "#   #", "    #", "   # ", "  #  ", " #   ", "#####"],
    '3': ["#####", "    #", "   # ", "  ## ", "    #", "#   #", " ### "],
    '4': ["   # ", "  ## ", " # # ", "#  # ", "#####", "   # ", "   # "],
    '5': ["#####", "#    ", "#    ", "#### ", "    #", "    #", "#### "],
    '6': [" ### ", "#    ", "#    ", "#### ", "#   #", "#   #", " ### "],
    '7': ["#####", "    #", "   # ", "  #  ", " #   ", " #   ", " #   "],
    '8': [" ### ", "#   #", "#   #", " ### ", "#   #", "#   #", " ### "],
    '9': [" ### ", "#   #", "#   #", " ####", "    #", "#   #", " ### "],
    ' ': ["     ", "     ", "     ", "     ", "     ", "     ", "     "],
    '!': ["  #  ", "  #  ", "  #  ", "  #  ", "  #  ", "     ", "  #  "],
    '?': [" ### ", "#   #", "    #", "   # ", "  #  ", "     ", "  #  "],
    '.': ["     ", "     ", "     ", "     ", "     ", "     ", "  #  "],
    '-': ["     ", "     ", "     ", "#####", "     ", "     ", "     "],
    '+': ["     ", "  #  ", "  #  ", "#####", "  #  ", "  #  ", "     "],
    '*': ["     ", "# # #", " ### ", "#####", " ### ", "# # #", "     "],
    '<': ["   # ", "  #  ", " #   ", "#    ", " #   ", "  #  ", "   # "],
    '>': ["#    ", " #   ", "  #  ", "   # ", "  #  ", " #   ", "#    "],
}


def pixel_count(string: str, letter_gap: int = 1) -> int:
    """Number of lit pixels — i.e. how many drones exactly fill the text."""
    total = 0
    for ch in string.upper():
        glyph = _FONT_5x7.get(ch, _FONT_5x7[' '])
        for row in glyph:
            total += row.count('#')
    return total


def text(
    string:     str,
    n:          int | None = None,
    scale_m:    float = 2.0,
    letter_gap: int   = 1,
    mirror:     bool  = True,
) -> list[tuple[float, float]]:
    """
    Return (dN, dE) positions for drones spelling the given string.

    n          Target drone count; pad with outer ring or subsample when the
               pixel count does not match.  Pass None to get exactly one drone
               per lit pixel.
    scale_m    Metres between adjacent pixel centres (default 2 m).
    letter_gap Blank pixel columns between characters (default 1).
    mirror     Flip the E axis so text reads L→R when viewed from below,
               i.e. audience-facing orientation (default True).
    """
    string = string.upper()
    pts: list[tuple[float, float]] = []
    col_offset = 0

    for ch in string:
        glyph  = _FONT_5x7.get(ch, _FONT_5x7[' '])
        char_h = len(glyph)
        char_w = max(len(row) for row in glyph)
        for row_idx, row_str in enumerate(glyph):
            for col_idx, px in enumerate(row_str):
                if px == '#':
                    dN = (char_h - 1 - row_idx) * scale_m   # row 0 top → highest N
                    dE = (col_offset + col_idx) * scale_m
                    pts.append((dN, dE))
        col_offset += char_w + letter_gap

    if mirror and pts:
        max_e = max(p[1] for p in pts)
        pts   = [(dN, max_e - dE) for dN, dE in pts]

    pts = _centre(pts)

    if n is not None:
        pts = _pad_to(pts, n)

    return pts


# ── Legacy 4-drone formation offsets ─────────────────────────────────────────

_LEGACY_4: dict[str, list[tuple[float, float]]] = {
    "diamond": [(-2.0, 0.0), (0.0, -2.0), (2.0, 0.0), (0.0,  2.0)],
    "arrow":   [( 0.0, 0.0), (2.0, -2.0), (2.0, 2.0), (4.0,  0.0)],
}

_DISPATCH = {
    "circle":  circle,
    "grid":    grid,
    "line":    line,
    "v_shape": v_shape,
    "v":       v_shape,
    "star":    star,
    "spiral":  spiral,
}


def get_formation(
    spec: str | list[tuple[float, float]],
    n:    int,
) -> list[tuple[float, float]]:
    """
    Return exactly n (dN, dE) offsets for the given formation spec.

    spec can be:
      "circle"              built-in generator
      "grid"                built-in generator
      "line"                built-in generator
      "v_shape" / "v"       built-in generator
      "star"                built-in generator
      "spiral"              built-in generator
      "diamond" / "arrow"   legacy 4-drone shapes (padded/subsampled to n)
      "text:HELLO"          sky-art text, 2 m pixel spacing
      "text:HELLO:scale=3"  sky-art text, 3 m pixel spacing
      [list of (dN,dE)]     custom positions (padded/subsampled to n)
    """
    if isinstance(spec, list):
        return _pad_to(spec, n)

    name = spec.lower()

    if name.startswith("text:"):
        parts   = name[5:].split(":")
        string  = parts[0]
        scale_m = 2.0
        for part in parts[1:]:
            if part.startswith("scale="):
                scale_m = float(part[6:])
        return text(string, n=n, scale_m=scale_m)

    # "grid:spacing=4" or "grid:cols=3:spacing=4" — keyword params after the name
    if ":" in name:
        base, *kv_parts = name.split(":")
        if base in _DISPATCH:
            kwargs: dict = {}
            for kv in kv_parts:
                if "=" in kv:
                    k, v = kv.split("=", 1)
                    kwargs[k.strip()] = float(v.strip())
            fn = _DISPATCH[base]
            import inspect
            valid = set(inspect.signature(fn).parameters) - {"n"}
            filtered = {k: v for k, v in kwargs.items() if k in valid}
            return fn(n, **filtered)

    if name in _DISPATCH:
        return _DISPATCH[name](n)

    if name in _LEGACY_4:
        return _pad_to(_LEGACY_4[name], n)

    available = sorted(list(_DISPATCH) + list(_LEGACY_4) + ["text:..."])
    raise ValueError(f"Unknown formation '{spec}'. Available: {available}")
