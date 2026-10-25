"""Run a fixed-budget series of rounds from successively published knowledge."""

from __future__ import annotations

import argparse
import json
import signal
import subprocess
from pathlib import Path

from mutagi.commands.inputs import add_plan_arguments, read_plan
from mutagi.commands.status import add_status_arguments, show_execution
from mutagi.execution.plans import validate_series
from mutagi.execution.series import Series, create_series
from mutagi.execution.state import lock, validate_id
from mutagi.project.repository import git


def main():
    parser = argparse.ArgumentParser(prog="mutagi series", description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    commands = parser.add_subparsers(dest="command", required=True)
    add_plan_arguments(commands.add_parser("start"), series=True)
    commands.add_parser("resume").add_argument("id", type=validate_id)
    add_status_arguments(commands.add_parser("status"), validate_id)

    args = parser.parse_args()
    repo = Path(git(args.repo, "rev-parse", "--show-toplevel")).resolve()
    if args.command == "start":
        plan = read_plan(args, validate_series, repo=repo)
        if args.dry_run:
            print(json.dumps(plan, ensure_ascii=False, indent=2))
            return
    if args.command == "status":
        show_execution(repo, "series", args.id, raw=args.json)
        return

    def interrupted(*_):
        raise KeyboardInterrupt("series interrupted")

    signal.signal(signal.SIGTERM, interrupted)
    with lock(repo):
        directory = (
            create_series(repo, plan)
            if args.command == "start"
            else repo / ".mutagi/runs" / "series" / args.id
        )
        print("Series: " + directory.name, flush=True)
        controller = Series(directory)
        try:
            result = controller.advance(recovery=args.command == "resume")
        except (
            OSError,
            RuntimeError,
            ValueError,
            subprocess.SubprocessError,
            KeyboardInterrupt,
        ) as error:
            controller.state["error"] = str(error)
            controller.save()
            print(
                f"Incomplete: {error}\nresume series {directory.name}",
                flush=True,
            )
            raise SystemExit(1) from error

        print(json.dumps(result, ensure_ascii=False, indent=2))
