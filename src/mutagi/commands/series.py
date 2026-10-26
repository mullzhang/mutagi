"""Typed commands for finite series execution and its checkpoints."""

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
from mutagi.commands.inputs import Base, DryRun, Plan, Steps, read_plan
from mutagi.commands.runs import advance, interruptible
from mutagi.commands.status import show_execution
from mutagi.execution.plans import validate_series
from mutagi.execution.series import Series, create_series
from mutagi.execution.state import lock
from mutagi.execution.service import manage

app = group(
    "Run a finite series, then inspect progress or recover its saved checkpoints."
)
app.callback()(configure)


@command(app)
def start(
    ctx: typer.Context,
    plan: Plan = None,
    step: Steps = [],
    base: Base = None,
    model: Model = None,
    effort: EffortOption = None,
    seconds: Annotated[
        float | None, typer.Option("--seconds", help="Total budget in seconds.")
    ] = None,
    experiment_seconds: Annotated[
        float | None,
        typer.Option("--experiment-seconds", help="Budget per exploration."),
    ] = None,
    integration_seconds: Annotated[
        float | None,
        typer.Option("--integration-seconds", help="Budget for knowledge integration."),
    ] = None,
    reserve_seconds: Annotated[
        float | None, typer.Option("--reserve-seconds", help="Preservation reserve.")
    ] = None,
    rounds: Annotated[
        int | None,
        typer.Option("--rounds", min=2, help="Finite number of repeated rounds."),
    ] = None,
    round_seconds: Annotated[
        float | None, typer.Option("--round-seconds", help="Budget per round.")
    ] = None,
    dry_run: DryRun = False,
    json: Json = False,
):
    """Start a finite series; --dry-run prints a reusable plan without execution."""
    repo = repository(ctx)
    resolved = usage(
        read_plan,
        "series",
        plan=plan,
        steps=step,
        rounds=rounds,
        overrides=supplied(
            base=base,
            model=model,
            effort=effort,
            seconds=seconds,
            experiment_seconds=experiment_seconds,
            integration_seconds=integration_seconds,
            reserve_seconds=reserve_seconds,
            round_seconds=round_seconds,
        ),
        repo=repo,
    )
    usage(validate_series, resolved)
    if dry_run:
        display.payload(resolved)
        return
    if ctx.parent.info_name == "service":
        with lock(repo):
            directory = create_series(repo, resolved)
            manage(repo, directory.name)
        from mutagi.commands.status import show_service

        if json:
            show_service(repo, raw=True)
        else:
            show_service(repo)
        return
    interruptible("series")
    with lock(repo):
        directory = create_series(repo, resolved)
        advance(repo, "series", Series(directory), recovery=False, machine=json)


@command(app)
def resume(ctx: typer.Context, id: Identifier, json: Json = False):
    """Recover remaining work with the original plan and ownership."""
    repo = repository(ctx)
    interruptible("series")
    with lock(repo):
        controller = Series(repo / ".mutagi/runs/series" / id)
        advance(repo, "series", controller, recovery=True, machine=json)


@command(app)
def status(
    ctx: typer.Context, id: Identifier, json: Json = False, details: Details = False
):
    """Inspect the recorded progress and current child without starting work."""
    show_execution(repository(ctx), "series", id, raw=json, details=details)
