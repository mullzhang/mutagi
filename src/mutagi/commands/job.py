"""Run one command with a deadline under a detached, recoverable POSIX supervisor."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from mutagi.commands.status import show_job
from mutagi.runtime.jobs import observe, read_state, recover, request_stop, start


def main():
    parser = argparse.ArgumentParser(prog="mutagi job", description=__doc__)
    subs = parser.add_subparsers(dest="action", required=True)
    launch = subs.add_parser("start")
    launch.add_argument("directory", type=Path)
    launch.add_argument("--cwd", required=True, type=Path)
    launch.add_argument("--seconds", required=True, type=float)
    launch.add_argument("--command", nargs=argparse.REMAINDER, required=True)
    for action in ("status", "stop", "recover"):
        command = subs.add_parser(action)
        command.add_argument("directory", type=Path)
        if action == "status":
            command.add_argument(
                "--json",
                action="store_true",
                help="Print the saved state JSON without process observations",
            )

    args = parser.parse_args()
    directory = args.directory.resolve()
    if args.action == "status" and args.json:
        print((directory / "state.json").read_text(), end="")
        return

    if args.action == "start":
        state = start(directory, args.cwd.resolve(), args.seconds, args.command)
    elif args.action == "recover":
        state = recover(directory)
    else:
        state = read_state(directory)
        if args.action == "stop":
            request_stop(directory)
        state = observe(state)

    if args.action == "status":
        show_job(directory, state)
    else:
        print(json.dumps(state, indent=2))
