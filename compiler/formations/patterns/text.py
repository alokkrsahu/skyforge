"""
text — sky-art words/letters from a 5×7 bitmap font.

Used via the "text:HELLO[:scale=N]" spec (the string is positional, so this pattern
is reached only through that prefix, not as a bare name). Holds the font table and
``pixel_count`` (how many drones exactly fill a string).
"""
from ..base import _centre, _pad_to, formation

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


@formation
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

    # _centre/_pad_to return 3-tuples; text is flat sky-art, so honour the (dN, dE)
    # contract for direct callers (get_formation re-adds dU=0 uniformly).
    return [(p[0], p[1]) for p in pts]
