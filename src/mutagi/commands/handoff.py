"""Preserve App results after the controller supplies an observed terminal state."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from mutagi.execution.handoff import accept
from mutagi.execution.state import lock


def main():
    parser = argparse.ArgumentParser(prog="mutagi handoff", description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--app-observation", type=Path, required=True)
    args = parser.parse_args()
    state = json.loads((args.run / "state.json").read_text())
    with lock(Path(state["repo"])):
        result = accept(args.run, json.loads(args.app_observation.read_text()))

    print(
        json.dumps(
            {
                key: result[key]
                for key in ("id", "phase", "saved_commit", "registry_commit")
            },
            indent=2,
        )
    )
