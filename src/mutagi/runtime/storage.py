"""Persist JSON checkpoints and read task files without changing line endings."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path


def atomic_json(path: Path, value) -> None:
    temporary = path.with_suffix(".tmp")
    with temporary.open("w") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())

    temporary.replace(path)


def utc(epoch: float) -> str:
    return (
        datetime.fromtimestamp(epoch, timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def read_text(path):
    with Path(path).open(encoding="utf-8", newline="") as stream:
        return stream.read()
