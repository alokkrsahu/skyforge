"""
Interactive REPL for drone fleet control.

Reads commands from stdin without blocking the asyncio event loop.
Dispatches to FleetCommander and prints results.
"""
import asyncio

from .commander import FleetCommander

HELP = """\
Commands:
  takeoff [alt]          — arm and ascend (default 5.0 m)
  land                   — staggered descent and disarm
  abort                  — emergency immediate land (no stagger)
  hover                  — cancel transition, hold in place

  circle                 — equally-spaced ring
  grid                   — rectangular grid
  line                   — east-west line
  v  / v_shape           — V-shape pointing north
  star                   — 5-point star
  spiral                 — Archimedean spiral

  A–Z                    — spell that letter with drones
  text <STRING>          — spell a word  (e.g.  text HELLO)

  Keyword variants:
    circle:radius_m=8    — circle with 8 m radius
    grid:spacing=4       — grid with 4 m spacing
    text:HI:scale=3      — text with 3 m pixel spacing

  move north|south|east|west <m>  — slide formation
  move <dN> <dE>         — NED offset (metres)
  alt <m>                — change cruise altitude
  color <name>           — set LED colour (red/green/blue/white/off/
                           orange/purple/cyan/yellow/pink)
  color <r> <g> <b>      — custom RGB (0.0–1.0 each)
  status                 — show drone positions and fleet state
  help                   — show this message
  quit / exit            — land and exit
"""

_DIRECTION_MAP = {
    "north": (1, 0), "south": (-1, 0),
    "east":  (0, 1), "west":  (0, -1),
    "n": (1, 0), "s": (-1, 0), "e": (0, 1), "w": (0, -1),
}

_FORMATION_WORDS = {"circle", "grid", "line", "star", "spiral"}


async def _dispatch(line: str, cmd: FleetCommander) -> str | None:
    parts = line.strip().split()
    if not parts:
        return None

    verb = parts[0].lower()

    if verb in ("quit", "exit"):
        if cmd.runtime.airborne:
            print(f"  {await cmd.land()}")
        return "__EXIT__"

    if verb in ("help", "?"):
        return HELP.rstrip()

    if verb == "takeoff":
        alt = float(parts[1]) if len(parts) > 1 else 5.0
        return await cmd.takeoff(alt)

    if verb == "land":
        return await cmd.land()

    if verb == "abort":
        return await cmd.abort()

    if verb == "hover":
        return await cmd.hover()

    if verb == "status":
        return await cmd.status()

    if verb == "text":
        if len(parts) < 2:
            return "Usage: text <STRING>"
        string = "".join(parts[1:]).upper()
        return await cmd.formation(f"text:{string}")

    # v / v_shape must be checked BEFORE the single-letter handler, otherwise
    # the bare "v" is caught as text:"V" (spell the letter) instead of the formation.
    if verb in ("v", "v_shape"):
        return await cmd.formation("v_shape")

    if len(verb) == 1 and verb.isalpha():
        return await cmd.formation(verb.upper())

    if verb in _FORMATION_WORDS or ":" in verb:
        spec = parts[0]
        t    = float(parts[1]) if len(parts) > 1 and "=" not in parts[1] else 6.0
        return await cmd.formation(spec, t)

    if verb == "move":
        if len(parts) < 3:
            return "Usage: move <north|south|east|west> <m>  or  move <dN> <dE>"
        if parts[1].lower() in _DIRECTION_MAP:
            dn, de = _DIRECTION_MAP[parts[1].lower()]
            dist   = float(parts[2])
            return await cmd.move(dn * dist, de * dist)
        return await cmd.move(float(parts[1]), float(parts[2]))

    if verb in ("alt", "altitude"):
        if len(parts) < 2:
            return "Usage: alt <metres>"
        return await cmd.set_altitude(float(parts[1]))

    if verb in ("color", "colour"):
        if len(parts) < 2:
            return "Usage: color <name>  or  color <r> <g> <b>"
        if len(parts) == 2:
            return await cmd.set_color(parts[1])
        return await cmd.set_color(float(parts[1]), float(parts[2]), float(parts[3]))

    # Last resort: try as formation name
    return await cmd.formation(verb)


async def cli_loop(commander: FleetCommander) -> None:
    loop = asyncio.get_running_loop()
    print("\nDrone Commander ready. Type 'help' for a full command list.\n")

    while True:
        try:
            line = await loop.run_in_executor(None, input, "(drone-cmd) > ")
        except (EOFError, KeyboardInterrupt):
            print("\nExiting.")
            if commander.runtime.airborne:
                print(await commander.abort())
            break

        try:
            result = await _dispatch(line, commander)
        except Exception as exc:
            result = f"Error: {exc}"

        if result == "__EXIT__":
            break
        if result:
            print(f"  {result}")
