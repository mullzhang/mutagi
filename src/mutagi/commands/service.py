"""Recover explicitly managed series after a process restart or login."""

from __future__ import annotations

import argparse
import json
import signal
from pathlib import Path

from mutagi.commands.inputs import add_plan_arguments, read_plan
from mutagi.commands.status import show_service
from mutagi.execution.plans import validate_series
from mutagi.execution.series import create_series
from mutagi.execution.service import HostShutdown, manage, request_pause, tick
from mutagi.execution.state import lock, validate_id
from mutagi.project.repository import git
from mutagi.runtime.launchd import install, uninstall


def main():
    parser = argparse.ArgumentParser(prog="mutagi service", description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("install")
    commands.add_parser("uninstall")
    commands.add_parser("tick")
    commands.add_parser("status").add_argument(
        "--json", action="store_true", help="Print the saved service state JSON"
    )
    add_plan_arguments(commands.add_parser("start"), series=True)
    commands.add_parser("manage").add_argument("id", type=validate_id)
    commands.add_parser("pause")
    args = parser.parse_args()
    repo = Path(git(args.repo, "rev-parse", "--show-toplevel")).resolve()

    def shutdown(*_):
        raise HostShutdown()

    signal.signal(signal.SIGTERM, shutdown)
    if args.command == "install":
        print(json.dumps(install(repo)))
    elif args.command == "tick":
        try:
            tick(repo)
        except HostShutdown:
            return
    elif args.command == "status":
        show_service(repo, raw=args.json)
    elif args.command == "pause":
        request_pause(repo)
        print(
            "Pause requested. The active task will be preserved before any further work starts."
        )
    elif args.command == "uninstall":
        uninstall(repo)
    else:
        if args.command == "start":
            plan = read_plan(args, validate_series, repo=repo)
            if args.dry_run:
                print(json.dumps(plan, ensure_ascii=False, indent=2))
                return
        with lock(repo):
            identifier = (
                create_series(repo, plan).name if args.command == "start" else args.id
            )
            manage(repo, identifier)
            print(identifier)
