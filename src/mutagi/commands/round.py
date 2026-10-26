"""Typed commands for finite round execution and its checkpoints."""

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
from mutagi.execution.plans import validate_round
from mutagi.execution.round import Round, create_round
from mutagi.execution.state import lock

app = group(
    "Run a finite round, then inspect progress or recover its saved checkpoints."
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
    dry_run: DryRun = False,
    json: Json = False,
):
    """Start a finite round; --dry-run prints a reusable plan without execution."""
    repo = repository(ctx)
    resolved = usage(
        read_plan,
        "round",
        plan=plan,
        steps=step,
        rounds=None,
        overrides=supplied(
            base=base,
            model=model,
            effort=effort,
            seconds=seconds,
            experiment_seconds=experiment_seconds,
            integration_seconds=integration_seconds,
            reserve_seconds=reserve_seconds,
        ),
        repo=repo,
    )
    usage(validate_round, resolved)
    if dry_run:
        display.payload(resolved)
        return
    interruptible("round")
    with lock(repo):
        directory = create_round(repo, resolved)
        advance(repo, "round", Round(directory), recovery=False, machine=json)


@command(app)
def resume(ctx: typer.Context, id: Identifier, json: Json = False):
    """Recover remaining work with the original plan and ownership."""
    repo = repository(ctx)
    interruptible("round")
    with lock(repo):
        controller = Round(repo / ".mutagi/runs/rounds" / id)
        if controller.state.get("series_id") is not None:
            raise RuntimeError(
                "Use mutagi series resume for a round owned by a series."
            )
        advance(repo, "round", controller, recovery=True, machine=json)


@command(app)
def status(
    ctx: typer.Context, id: Identifier, json: Json = False, details: Details = False
):
    """Inspect the recorded progress and current child without starting work."""
    show_execution(repository(ctx), "round", id, raw=json, details=details)
