"""Resolve explicit command inputs before allocating execution state."""

from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy

from mutagi.execution.plans import resolve_settings
from mutagi.project.settings import EFFORTS, EXECUTION_FIELDS
from mutagi.runtime import storage

PLAN_OVERRIDES = ("base", *EXECUTION_FIELDS["series"])


def read_text(source):
    if str(source) == "-":
        return sys.stdin.read()
    return storage.read_text(source)


class TaskArgument(argparse.Action):
    """Retain independent and inherited tasks in command-line order."""

    def __call__(self, parser, namespace, values, option_string=None):
        parent = None
        prompt = values
        if option_string == "--inherit":
            index, prompt = values
            try:
                parent = int(index)
            except ValueError as error:
                raise argparse.ArgumentError(
                    self, "parent index must be an integer"
                ) from error
        tasks = list(getattr(namespace, self.dest, None) or [])
        tasks.append({"prompt": prompt, "parent": parent})
        setattr(namespace, self.dest, tasks)


def add_plan_arguments(parser, *, series=False):
    parser.set_defaults(plan_kind="series" if series else "round")
    parser.add_argument("--plan", help="Plan JSON file, or - for standard input")
    parser.add_argument(
        "--prompt",
        dest="task_arguments",
        action=TaskArgument,
        help="Append an independent task; repeat in execution order",
    )
    parser.add_argument(
        "--inherit",
        dest="task_arguments",
        action=TaskArgument,
        nargs=2,
        metavar=("INDEX", "TEXT"),
        help="Append a task inheriting an earlier zero-based task index",
    )
    if series:
        parser.add_argument(
            "--rounds",
            type=int,
            help="Repeat the task sequence this many times (at least 2)",
        )
    for field in PLAN_OVERRIDES:
        if field == "round_seconds" and not series:
            continue
        options = {"default": argparse.SUPPRESS, "help": "Set or override " + field}
        if field.endswith("seconds"):
            options["type"] = float
        elif field == "effort":
            options["choices"] = EFFORTS
        parser.add_argument("--" + field.replace("_", "-"), **options)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and print the resolved plan JSON without creating or running anything",
    )


def read_plan(args, validate, *, repo=None):
    tasks = args.task_arguments
    rounds = getattr(args, "rounds", None)
    if args.plan:
        if tasks is not None or rounds is not None:
            raise ValueError(
                "--plan cannot be combined with --prompt, --inherit, or --rounds"
            )
        try:
            plan = json.loads(read_text(args.plan))
        except json.JSONDecodeError as error:
            raise ValueError(f"{args.plan}: invalid plan JSON: {error}") from error
        if not isinstance(plan, dict):
            raise ValueError(f"{args.plan}: the plan must be a JSON object")
    else:
        if not tasks:
            raise ValueError(
                "provide --plan or a task sequence using --prompt and --inherit"
            )
        if args.plan_kind == "series":
            if rounds is None or rounds < 2:
                raise ValueError(
                    "provide --rounds with at least two rounds for a CLI task sequence"
                )
            plan = {"rounds": [{"tasks": deepcopy(tasks)} for _ in range(rounds)]}
        else:
            plan = {"tasks": tasks}
    plan.update(
        {field: vars(args)[field] for field in PLAN_OVERRIDES if field in vars(args)}
    )
    if repo is not None:
        plan = resolve_settings(repo, plan.get("base"), args.plan_kind, plan)
    validate(plan)
    return plan
