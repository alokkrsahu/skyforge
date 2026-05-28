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
