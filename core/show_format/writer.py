"""Serialize a ShowFile to JSON or msgpack."""
from __future__ import annotations

import dataclasses
import json
import msgpack

from .schema import ShowFile


def _to_dict(obj):
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {k: _to_dict(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, list):
        return [_to_dict(i) for i in obj]
    return obj


def to_json(show: ShowFile, path: str, indent: int = 2) -> None:
    with open(path, "w") as f:
        json.dump(_to_dict(show), f, indent=indent)


def to_msgpack(show: ShowFile, path: str) -> None:
    with open(path, "wb") as f:
        f.write(msgpack.packb(_to_dict(show), use_bin_type=True))


def single_drone_show(show: ShowFile, drone_id: int) -> ShowFile:
    """A valid 1-drone ShowFile holding only ``drone_id``'s trajectory/LED/envelope,
    renumbered to id 0. The unit a drone needs to fly its own part autonomously
    (upload-and-go) — round-trips through ``reader.from_json`` (n_drones == 1)."""
    md     = dataclasses.replace(show.metadata, n_drones=1,
                                 name=f"{show.metadata.name} [drone {drone_id}]")
    drones = [dataclasses.replace(show.drones[drone_id], logical_id=0)]
    trajs  = [dataclasses.replace(show.trajectories[drone_id], drone_id=0)]
    leds   = ([dataclasses.replace(show.led_tracks[drone_id], drone_id=0)]
              if drone_id < len(show.led_tracks) else [])
    envs   = ([dataclasses.replace(show.envelopes[drone_id], drone_id=0)]
              if drone_id < len(show.envelopes) else [])
    return dataclasses.replace(show, metadata=md, drones=drones, trajectories=trajs,
                               led_tracks=leds, envelopes=envs, reactive_bindings=[])


def to_json_trajectory(show: ShowFile, drone_id: int, path: str, indent: int = 2) -> None:
    """Write a single drone's trajectory slice (see ``single_drone_show``) to JSON."""
    to_json(single_drone_show(show, drone_id), path, indent=indent)
