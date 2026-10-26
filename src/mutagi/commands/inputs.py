"""Resolve typed CLI instructions and finite plans before allocating state."""

from copy import deepcopy
from dataclasses import dataclass
import json
import sys
from typing import Annotated

import typer

from mutagi.execution.plans import resolve_settings
from mutagi.runtime import storage


def read_text(source):
    if str(source) == "-":
        return sys.stdin.read()
    return storage.read_text(source)


@dataclass(frozen=True)
class Step:
    prompt: str
    parent: int | None


def parse_step(value):
    parent, separator, prompt = value.partition(":")
    if not separator or not prompt.strip():
        raise typer.BadParameter(
            "Use --step 'none: instruction' or --step '0: inherited instruction'."
        )
    try:
        index = None if parent.strip() == "none" else int(parent.strip())
    except ValueError as error:
        raise typer.BadParameter(
            "The step parent must be 'none' or an earlier zero-based index."
        ) from error
    return Step(prompt.lstrip(), index)


Steps = Annotated[
    list[Step],
    typer.Option(
        "--step",
        parser=parse_step,
        metavar="PARENT:TEXT",
        help="Ordered task; repeat 'none: instruction' or '0: inherited instruction'.",
    ),
]
Plan = Annotated[
    str | None,
    typer.Option(
        "--plan",
        help="Finite plan JSON file, or - for stdin; excludes --step/--rounds.",
    ),
]
Base = Annotated[
    str | None,
    typer.Option("--base", help="Committed base; required unless supplied by --plan."),
]
DryRun = Annotated[
    bool,
    typer.Option(
        "--dry-run",
        help="Validate and print reusable JSON without starting or saving work.",
    ),
]


def read_plan(kind, *, plan, steps, rounds, overrides, repo=None):
    if plan is not None:
        if steps or rounds is not None:
            raise ValueError("--plan cannot be combined with --step or --rounds")
        try:
            value = json.loads(read_text(plan))
        except json.JSONDecodeError as error:
            raise ValueError(f"{plan}: invalid plan JSON: {error}") from error
        if not isinstance(value, dict):
            raise ValueError(f"{plan}: the plan must be a JSON object")
    else:
        if not steps:
            raise ValueError(
                "provide --plan or an ordered sequence of --step PARENT:TEXT"
            )
        tasks = [{"prompt": step.prompt, "parent": step.parent} for step in steps]
        if kind == "series":
            if rounds is None or rounds < 2:
                raise ValueError(
                    "provide --rounds with at least two rounds for a task sequence"
                )
            value = {"rounds": [{"tasks": deepcopy(tasks)} for _ in range(rounds)]}
        else:
            value = {"tasks": tasks}
    value.update(overrides)
    if repo is not None:
        value = resolve_settings(repo, value.get("base"), kind, value)
    return value
