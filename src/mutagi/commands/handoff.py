"""Verify supplied App observations and preserve their recorded results."""

import json as json_module
from pathlib import Path
from typing import Annotated

import typer

from mutagi.commands import display
from mutagi.commands.common import Json
from mutagi.commands.status import show_execution
from mutagi.execution.handoff import accept
from mutagi.execution.state import lock


def handoff_result(
    run: Annotated[
        Path,
        typer.Option(
            "--run",
            exists=True,
            file_okay=False,
            help="Recorded App execution directory.",
        ),
    ],
    app_observation: Annotated[
        Path,
        typer.Option(
            "--app-observation",
            exists=True,
            dir_okay=False,
            help="Controller-confirmed terminal observation JSON.",
        ),
    ],
    json: Json = False,
):
    state = json_module.loads((run / "state.json").read_text())
    repo = Path(state["repo"])
    with lock(repo):
        result = accept(run, json_module.loads(app_observation.read_text()))
    if json:
        display.payload(
            {
                key: result[key]
                for key in ("id", "phase", "saved_commit", "registry_commit")
            }
        )
    else:
        display.title("App result preserved")
        show_execution(repo, "experiment", result["id"])
