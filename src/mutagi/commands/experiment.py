"""Run and preserve one exploration. Requires Python 3.12+ and POSIX."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
from pathlib import Path

from mutagi.commands.inputs import read_text
from mutagi.commands.status import add_status_arguments, show_execution
from mutagi.execution.experiment import Runner, create
from mutagi.execution.plans import (
    resolve_settings,
    validate_experiment,
    validate_parent,
    validate_task,
)
from mutagi.execution.state import lock, validate_id
from mutagi.project.repository import git
from mutagi.project.settings import EFFORTS, EXECUTION_FIELDS


def main():
    parser = argparse.ArgumentParser(prog="mutagi experiment", description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    commands = parser.add_subparsers(dest="command", required=True)
    start = commands.add_parser("start")
    start.add_argument("--base", required=True)
    instruction = start.add_mutually_exclusive_group(required=True)
    instruction.add_argument("--task", help="Instruction file, or - for standard input")
    instruction.add_argument("--prompt", help="Instruction text for this exploration")
    start.add_argument("--model", default=argparse.SUPPRESS)
    start.add_argument(
        "--effort",
        default=argparse.SUPPRESS,
        choices=EFFORTS,
    )
    start.add_argument(
        "--seconds",
        type=float,
        default=argparse.SUPPRESS,
        help="Total seconds (default: saved setting, otherwise 3600)",
    )
    start.add_argument(
        "--reserve-seconds",
        type=float,
        default=argparse.SUPPRESS,
        help="Preservation reserve (default: saved setting, otherwise 600)",
    )
    start.add_argument("--parent-id")
    start.add_argument("--parent-record")
    start.add_argument(
        "--dry-run",
        action="store_true",
        help="Print resolved inputs as JSON without creating or running anything",
    )
    commands.add_parser("resume").add_argument("id", type=validate_id)
    add_status_arguments(commands.add_parser("status"), validate_id)

    args = parser.parse_args()
    if args.command == "start":
        task_text = validate_task(
            args.prompt if args.prompt is not None else read_text(args.task)
        )
    repo = Path(git(args.repo, "rev-parse", "--show-toplevel")).resolve()
    if os.name != "posix":
        parser.error("this runner requires POSIX process groups and flock")

    if args.command == "status":
        show_execution(repo, "experiment", args.id, raw=args.json)
        return

    if args.command == "start":
        settings = {
            "seconds": 3600,
            "reserve_seconds": 600,
            **resolve_settings(
                repo,
                args.base,
                "experiment",
                {
                    field: vars(args)[field]
                    for field in EXECUTION_FIELDS["experiment"]
                    if field in vars(args)
                },
            ),
        }
        if not {"model", "effort"} <= settings.keys():
            raise ValueError(
                "provide --model and --effort or save explicit execution defaults at the base commit"
            )
        validate_experiment(
            task_text,
            settings["model"],
            settings["effort"],
            settings["seconds"],
            settings["reserve_seconds"],
        )
        validate_parent(repo, settings["base"], args.parent_id, args.parent_record)
        if args.dry_run:
            print(
                json.dumps(
                    {
                        **settings,
                        "prompt": task_text,
                        "parent_id": args.parent_id,
                        "parent_record": args.parent_record,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return

    def interrupted(*_):
        raise KeyboardInterrupt("runner interrupted")

    signal.signal(signal.SIGTERM, interrupted)
    with lock(repo):
        if args.command == "start":
            run = create(
                repo,
                settings["base"],
                task_text,
                settings["model"],
                settings["effort"],
                settings["seconds"],
                settings["reserve_seconds"],
                args.parent_id,
                args.parent_record,
            )
        else:
            run = repo / ".mutagi/runs" / args.id

        print(f"Run: {run.name}", flush=True)
        runner = Runner(run)
        if runner.state.get("series_id") is not None:
            raise RuntimeError("use mutagi series resume for a run owned by a series")

        try:
            result = runner.advance(recovery=args.command == "resume")
        except (
            OSError,
            RuntimeError,
            ValueError,
            subprocess.SubprocessError,
            KeyboardInterrupt,
        ) as error:
            runner.state["error"] = str(error)
            runner.save()
            print(
                f"Incomplete: {error}\nRecovery command: mutagi experiment resume {run.name}"
            )
            raise SystemExit(1) from error

        print(
            json.dumps(
                {
                    "id": result["id"],
                    "phase": result["phase"],
                    "reason": result["reason"],
                    "saved_commit": result.get("saved_commit"),
                },
                ensure_ascii=False,
            )
        )
