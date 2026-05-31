"""
Fleet broadcast channel + link-loss policy.

At scale you don't send START/ABORT down N point-to-point links — you BROADCAST one
command to the whole fleet, and each agent decides what to do if it stops hearing the
ground. This provides:

  * ``FleetBroadcast`` — a crude-but-real one-command channel backed by a shared file
    (atomic publish + monotonic seq so receivers detect a new command). It works across
    processes and hosts on a shared filesystem (SITL / a lab LAN), and presents the exact
    ``publish()`` / ``latest()`` contract a UDP-multicast or RF transport would.
  * ``link_loss_action`` — what a drone should do when the broadcast goes quiet: ride
    through a brief gap (None), then HOLD, then LAND — fail safe without a ground link.

DEFERRED (hardware): the real RF / multicast transport (swap the file for a socket; the
contract is unchanged), and encryption/authentication of the command stream.
"""
from __future__ import annotations

import json
import os

COMMANDS = ("start", "abort", "hold", "rtl")


class FleetBroadcast:
    def __init__(self, path: str):
        self.path = path

    def publish(self, command: str, epoch: float | None = None) -> dict:
        """Broadcast a fleet command (optionally with a shared start epoch). Atomic +
        monotonically-sequenced so every receiver can tell it's new."""
        command = command.lower()
        if command not in COMMANDS:
            raise ValueError(f"unknown broadcast command {command!r}; use {COMMANDS}")
        seq = (self.latest() or {}).get("seq", 0) + 1
        rec = {"seq": seq, "command": command, "epoch": epoch}
        tmp = f"{self.path}.tmp"
        with open(tmp, "w") as f:
            json.dump(rec, f)
        os.replace(tmp, self.path)                         # atomic swap — no torn reads
        return rec

    def latest(self) -> dict | None:
        try:
            with open(self.path) as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return None


def link_loss_action(stale_for_s: float, *, hold_grace_s: float = 2.0,
                     land_after_s: float = 10.0) -> str | None:
    """Fail-safe ladder when the broadcast goes quiet for ``stale_for_s`` seconds:
    ride a brief gap (``None``), then ``"hold"``, then ``"land"`` — autonomous, no link."""
    if stale_for_s <= hold_grace_s:
        return None
    if stale_for_s <= land_after_s:
        return "hold"
    return "land"
