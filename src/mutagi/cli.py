"""The public Typer command tree; execution and workers remain separate."""

from importlib.metadata import version
from typing import Annotated

import typer

from mutagi.commands import (
    check,
    experiment,
    handoff,
    harvest,
    init,
    job,
    round,
    series,
    service,
)
from mutagi.commands.common import command, group
from mutagi.commands import display
from mutagi.prompts import record_template

app = group(
    "Investigate questions, preserve evidence, and reuse useful results. Start with init or check; browse saved work with harvest."
)


def version_option(value: bool):
    if value:
        typer.echo("mutagi " + version("mutagi"))
        raise typer.Exit()


@app.callback()
def root(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=version_option,
            is_eager=True,
            help="Print the installed version.",
        ),
    ] = False,
):
    """Explore → preserve → review → reuse. Use COMMAND --help for actions."""


command(app, "init", help="Configure a project and review generated files.")(
    init.initialize_project
)
command(app, "check", help="Check readiness and items needing attention.")(
    check.check_project
)
app.add_typer(
    experiment.app, name="experiment", help="Run, preserve, or inspect one exploration."
)
app.add_typer(
    round.app, name="round", help="Run a finite round and integrate its knowledge."
)
app.add_typer(
    series.app, name="series", help="Run successive rounds within a fixed budget."
)
app.add_typer(
    service.app, name="service", help="Manage automatic recovery of an assigned series."
)
app.add_typer(
    job.app, name="job", help="Supervise an external command with a deadline."
)
app.add_typer(harvest.app, name="harvest", help="Find, try, and reuse saved results.")
command(app, "handoff", help="Verify and preserve controller-confirmed App results.")(
    handoff.handoff_result
)
templates = group("Print reusable unstyled templates.")


@command(templates, "experiment")
def experiment_template():
    """Print the exact experiment record template to stdout."""
    display.text_payload(record_template())


app.add_typer(templates, name="template")


def main():
    app(prog_name="mutagi")
