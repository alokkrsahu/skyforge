"""
Skyforge command-line interface.

Usage:
    skyforge compile  <show_script.py>  [-o DIR]  [--min-sep M]  [--no-validate]
    skyforge validate <show.skyforge.json>  [--min-sep M]
    skyforge info     <show.skyforge(.json)>

Example:
    skyforge compile shows/four_drone_demo.py
    skyforge validate shows/four_drone_demo.skyforge.json
    skyforge info     shows/four_drone_demo.skyforge.json
"""
from __future__ import annotations

import argparse
import os
import runpy
import sys


def _add_skyforge_to_path() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)


# ── Sub-commands ──────────────────────────────────────────────────────────────

def cmd_compile(args: argparse.Namespace) -> int:
    _add_skyforge_to_path()
    from compiler.pipeline import CompileConfig, CompilePipeline
    from compiler.envelope import EnvelopeConfig
    from compiler.validator import ValidationConfig
    from core.show_format.writer import to_json, to_msgpack

    script = os.path.abspath(args.script)
    if not os.path.isfile(script):
        print(f"ERROR: script not found: {script}", file=sys.stderr)
        return 1

    out_dir = os.path.abspath(args.output) if args.output else os.path.dirname(script)
    base    = os.path.splitext(os.path.basename(script))[0]

    cfg = CompileConfig(
        envelope   = EnvelopeConfig(min_sep_m=args.min_sep),
        validation = ValidationConfig(min_sep_m=args.min_sep,
                                      tracking_margin_m=getattr(args, "tracking_margin", 0.0)),
        compute_envelopes = True,
        validate          = not args.no_validate,
        fail_on_error     = False,   # CLI always prints result; caller decides
    )

    print(f"[skyforge] Compiling {os.path.basename(script)} ...")
    ns      = runpy.run_path(script)
    builder = ns.get("builder")
    if builder is None:
        print("ERROR: show script must define a module-level 'builder' variable.", file=sys.stderr)
        return 1

    pipeline = CompilePipeline(cfg)
    try:
        result = pipeline.run(builder)
    except Exception as exc:
        print(f"ERROR during compilation: {exc}", file=sys.stderr)
        return 1

    show = result.show
    if result.validation:
        print(result.validation)
        if not result.validation.passed:
            print("[skyforge] Compilation finished with validation errors — output NOT written.")
            return 1

    json_path = os.path.join(out_dir, f"{base}.skyforge.json")
    bin_path  = os.path.join(out_dir, f"{base}.skyforge")
    to_json(show, json_path)
    to_msgpack(show, bin_path)
    print(
        f"[skyforge] Written:\n"
        f"  {json_path}\n"
        f"  {bin_path}\n"
        f"  {show.metadata.n_drones} drones  "
        f"{show.metadata.duration_s:.0f}s  "
        f"{sum(len(t.segments) for t in show.trajectories)} segments"
    )
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    _add_skyforge_to_path()
    from compiler.validator import ValidationConfig, validate
    from core.show_format.reader import from_json, from_msgpack

    path = os.path.abspath(args.show)
    if not os.path.isfile(path):
        print(f"ERROR: file not found: {path}", file=sys.stderr)
        return 1

    show = from_json(path) if path.endswith(".json") else from_msgpack(path)
    cfg  = ValidationConfig(min_sep_m=args.min_sep,
                            tracking_margin_m=getattr(args, "tracking_margin", 0.0))
    result = validate(show, cfg)
    print(result)
    return 0 if result.passed else 1


def cmd_info(args: argparse.Namespace) -> int:
    _add_skyforge_to_path()
    from core.show_format.reader import from_json, from_msgpack

    path = os.path.abspath(args.show)
    if not os.path.isfile(path):
        print(f"ERROR: file not found: {path}", file=sys.stderr)
        return 1

    show = from_json(path) if path.endswith(".json") else from_msgpack(path)
    m    = show.metadata
    print(f"Name            : {m.name}")
    print(f"Author          : {m.author or '(none)'}")
    print(f"Venue           : {m.venue_name or '(none)'}")
    print(f"Created         : {m.created_at}")
    print(f"Schema version  : {m.schema_version}")
    print(f"Validation      : {m.validation_status}")
    print(f"Drones          : {m.n_drones}")
    print(f"Duration        : {m.duration_s:.1f} s")
    total_segs = sum(len(t.segments) for t in show.trajectories)
    print(f"Traj segments   : {total_segs}")
    print(f"LED tracks      : {len(show.led_tracks)}")
    print(f"Reactive bindings: {len(show.reactive_bindings)}")
    if show.reactive_bindings:
        for b in show.reactive_bindings:
            drones = b.drone_ids or list(range(m.n_drones))
            print(f"  [{b.t_start:.0f}s–{b.t_end:.0f}s] {b.primitive}  drones={drones}")
    return 0


# ── Entry point ───────────────────────────────────────────────────────────────

def cmd_export(args: argparse.Namespace) -> int:
    _add_skyforge_to_path()
    from core.show_format.reader import from_json, from_msgpack
    from core.show_format.writer import to_json_trajectory

    path = os.path.abspath(args.show)
    if not os.path.isfile(path):
        print(f"ERROR: file not found: {path}", file=sys.stderr)
        return 1

    show = from_json(path) if path.endswith(".json") else from_msgpack(path)
    n    = show.metadata.n_drones
    if args.all:
        ids = list(range(n))
    elif args.drone is not None and 0 <= args.drone < n:
        ids = [args.drone]
    else:
        print(f"ERROR: pass --all or --drone N with 0 <= N < {n}", file=sys.stderr)
        return 1

    out_dir = os.path.abspath(args.output) if args.output else os.path.dirname(path)
    base    = os.path.basename(path).split(".skyforge")[0]
    for i in ids:
        to_json_trajectory(show, i, os.path.join(out_dir, f"{base}.drone{i:03d}.skyforge.json"))
    print(f"[skyforge] Exported {len(ids)} trajectory slice(s) → {out_dir}")
    return 0


def cmd_energy(args: argparse.Namespace) -> int:
    _add_skyforge_to_path()
    from core.show_format.reader import from_json, from_msgpack
    from compiler.energy import estimate_energy, EnergyModel

    path = os.path.abspath(args.show)
    if not os.path.isfile(path):
        print(f"ERROR: file not found: {path}", file=sys.stderr)
        return 1
    show = from_json(path) if path.endswith(".json") else from_msgpack(path)
    rep  = estimate_energy(show, EnergyModel(endurance_hover_s=args.endurance,
                                             reserve_frac=args.reserve))
    print(f"Duration        : {rep.duration_s:.0f} s")
    print(f"Worst drone     : #{rep.worst_drone}  using ~{rep.max_used_frac:.0%} of a charge")
    print(f"Reserve target  : land with >= {args.reserve:.0%}")
    print(f"Verdict         : {'OK' if rep.fits else 'OVER BUDGET — shorten the show or raise endurance'}")
    return 0 if rep.fits else 1


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="skyforge",
        description="Skyforge drone show platform — compiler & validator",
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    # compile
    p = sub.add_parser("compile", help="Compile a show script to .skyforge files")
    p.add_argument("script",        help="Path to show Python script")
    p.add_argument("-o", "--output", default=None, metavar="DIR",
                   help="Output directory (default: same as script)")
    p.add_argument("--min-sep",     type=float, default=1.5, metavar="M",
                   help="Minimum inter-drone separation in metres (default: 1.5)")
    p.add_argument("--tracking-margin", type=float, default=0.0, metavar="M",
                   help="Extra separation headroom for real tracking error (default: 0)")
    p.add_argument("--no-validate", action="store_true",
                   help="Skip validation after compilation")

    # validate
    p = sub.add_parser("validate", help="Validate an existing .skyforge file")
    p.add_argument("show",      help="Path to .skyforge or .skyforge.json")
    p.add_argument("--min-sep", type=float, default=1.5, metavar="M")
    p.add_argument("--tracking-margin", type=float, default=0.0, metavar="M",
                   help="Extra separation headroom for real tracking error (default: 0)")

    # info
    p = sub.add_parser("info", help="Print show metadata")
    p.add_argument("show", help="Path to .skyforge or .skyforge.json")

    # export — per-drone trajectory slices (upload-and-go foundation)
    p = sub.add_parser("export", help="Export per-drone trajectory slices to JSON")
    p.add_argument("show", help="Path to .skyforge or .skyforge.json")
    p.add_argument("--drone", type=int, default=None, metavar="N", help="Export only drone N")
    p.add_argument("--all", action="store_true", help="Export every drone's slice")
    p.add_argument("-o", "--output", default=None, metavar="DIR",
                   help="Output directory (default: same as the show)")

    # energy — battery budget check
    p = sub.add_parser("energy", help="Estimate per-drone battery usage of a show")
    p.add_argument("show", help="Path to .skyforge or .skyforge.json")
    p.add_argument("--endurance", type=float, default=600.0, metavar="S",
                   help="Full-charge hover endurance in seconds (default: 600)")
    p.add_argument("--reserve", type=float, default=0.20, metavar="F",
                   help="Required landing reserve fraction (default: 0.20)")

    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        sys.exit(1)

    handlers = {"compile": cmd_compile, "validate": cmd_validate,
                "info": cmd_info, "export": cmd_export, "energy": cmd_energy}
    sys.exit(handlers[args.command](args))


if __name__ == "__main__":
    main()
