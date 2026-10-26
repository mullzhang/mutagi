"""Typed external command supervision with human output or clean JSON."""

from pathlib import Path
from typing import Annotated

import typer

from mutagi.commands import display
from mutagi.commands.common import Details, Json, command, group
from mutagi.commands.status import show_job
from mutagi.runtime.jobs import (
    observe,
    read_state,
    recover as recover_job,
    request_stop,
    start as start_job,
)

app = group(
    "Run an external command with a deadline, inspect its supervisor, or recover it."
)
Directory = Annotated[Path, typer.Argument(help="Dedicated job record directory.")]


@command(app)
def start(
    directory: Directory,
    cwd: Annotated[
        Path,
        typer.Option(
            "--cwd", exists=True, file_okay=False, help="Command working directory."
        ),
    ],
    seconds: Annotated[
        float, typer.Option("--seconds", help="Time budget including stopping.")
    ],
    command: Annotated[
        list[str], typer.Argument(help="External command and arguments, after --.")
    ],
    json: Json = False,
):
    """Start a supervised command: DIRECTORY --cwd PATH --seconds N -- COMMAND ARGS."""
    directory = directory.resolve()
    state = start_job(directory, cwd.resolve(), seconds, command)
    if json:
        display.payload(state)
    else:
        show_job(directory, observe(state))


@command(app)
def status(directory: Directory, json: Json = False, details: Details = False):
    """Read the saved state, or additionally observe the supervisor for human output."""
    directory = directory.resolve()
    if json:
        display.text_payload((directory / "state.json").read_text())
    else:
        show_job(directory, observe(read_state(directory)), details=details)


@command(app)
def stop(directory: Directory, json: Json = False):
    """Request a stop; the supervisor owns process termination and preservation."""
    directory = directory.resolve()
    state = read_state(directory)
    request_stop(directory)
    if json:
        display.payload(observe(state))
    else:
        display.title("Stop requested")
        show_job(directory, observe(state))


@command(app)
def recover(directory: Directory, json: Json = False):
    """Recover recorded process ownership without rerunning the command."""
    directory = directory.resolve()
    state = recover_job(directory)
    if json:
        display.payload(state)
    else:
        show_job(directory, observe(state))
