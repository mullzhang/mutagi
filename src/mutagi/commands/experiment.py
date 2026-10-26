"""Typed commands for one exploration and its preservation checkpoints."""

import os
from typing import Annotated

import typer

from mutagi.commands import display
from mutagi.commands.common import (
    Details,
    EffortOption,
    Identifier,
    Json,
    Model,
    command,
    configure,
    group,
    repository,
    supplied,
    usage,
)
from mutagi.commands.inputs import DryRun, read_text
from mutagi.commands.runs import advance, interruptible
from mutagi.commands.status import show_execution
from mutagi.execution.experiment import Runner, create
from mutagi.execution.plans import (
    resolve_settings,
    validate_experiment,
    validate_parent,
    validate_task,
)
from mutagi.execution.state import lock

app = group(
    "Run one independent or inherited exploration, preserve it, then inspect or recover its checkpoints."
)
app.callback()(configure)


@command(app)
def start(
    ctx: typer.Context,
    base: Annotated[str, typer.Option("--base", help="Fixed starting revision.")],
    prompt: Annotated[
        str | None, typer.Option("--prompt", help="Instruction text; excludes --task.")
    ] = None,
    task: Annotated[
        str | None,
        typer.Option("--task", help="UTF-8 instruction file, or - for stdin."),
    ] = None,
    model: Model = None,
    effort: EffortOption = None,
    seconds: Annotated[
        float | None,
        typer.Option("--seconds", help="Total budget including preservation."),
    ] = None,
    reserve_seconds: Annotated[
        float | None,
        typer.Option(
            "--reserve-seconds", help="Preservation time within the total budget."
        ),
    ] = None,
    parent_id: Annotated[
        str | None,
        typer.Option("--parent-id", help="Parent UUID; requires --parent-record."),
    ] = None,
    parent_record: Annotated[
        str | None,
        typer.Option("--parent-record", help="Parent observation at --base."),
    ] = None,
    dry_run: DryRun = False,
    json: Json = False,
):
    """Start an exploration, or preview its resolved inputs without executing."""
    if (prompt is None) == (task is None):
        raise typer.BadParameter("Provide exactly one of --prompt and --task.")
    text = usage(
        validate_task, prompt if prompt is not None else usage(read_text, task)
    )
    repo = repository(ctx)
    if os.name != "posix":
        raise typer.BadParameter("This runner requires POSIX process groups and flock.")
    settings = {
        "seconds": 3600,
        "reserve_seconds": 600,
        **usage(
            resolve_settings,
            repo,
            base,
            "experiment",
            supplied(
                model=model,
                effort=effort,
                seconds=seconds,
                reserve_seconds=reserve_seconds,
            ),
        ),
    }
    if not {"model", "effort"} <= settings.keys():
        raise typer.BadParameter(
            "Provide --model and --effort or save explicit execution defaults at the base commit."
        )
    usage(
        validate_experiment,
        text,
        settings["model"],
        settings["effort"],
        settings["seconds"],
        settings["reserve_seconds"],
    )
    usage(validate_parent, repo, settings["base"], parent_id, parent_record)
    if dry_run:
        display.payload(
            {
                **settings,
                "prompt": text,
                "parent_id": parent_id,
                "parent_record": parent_record,
            }
        )
        return
    interruptible("experiment")
    with lock(repo):
        directory = create(
            repo,
            settings["base"],
            text,
            settings["model"],
            settings["effort"],
            settings["seconds"],
            settings["reserve_seconds"],
            parent_id,
            parent_record,
        )
        advance(repo, "experiment", Runner(directory), recovery=False, machine=json)


@command(app)
def resume(ctx: typer.Context, id: Identifier, json: Json = False):
    """Recover unfinished preservation using the original execution ID."""
    repo = repository(ctx)
    interruptible("experiment")
    with lock(repo):
        runner = Runner(repo / ".mutagi/runs" / id)
        if runner.state.get("series_id") is not None:
            raise RuntimeError("Use mutagi series resume for a run owned by a series.")
        advance(repo, "experiment", runner, recovery=True, machine=json)


@command(app)
def status(
    ctx: typer.Context, id: Identifier, json: Json = False, details: Details = False
):
    """Inspect recorded state and current process observations without changing them."""
    show_execution(repository(ctx), "experiment", id, raw=json, details=details)
