"""Typer definitions and error boundaries shared by public commands."""

from enum import Enum
from functools import wraps
from pathlib import Path
import shlex
import subprocess
from typing import Annotated

import typer

from mutagi.commands import display
from mutagi.execution.state import validate_id
from mutagi.project.repository import repository_root
from mutagi.project.settings import EFFORTS

Effort = Enum("Effort", {name: name for name in EFFORTS}, type=str)
Repo = Annotated[
    Path,
    typer.Option(
        "--repo",
        help="Exploration repository (default: current directory).",
        show_default=False,
    ),
]
Json = Annotated[bool, typer.Option("--json", help="Write undecorated JSON to stdout.")]
Details = Annotated[
    bool,
    typer.Option(
        "--details",
        help="Include supporting checkpoints, settings, and full references.",
    ),
]
Model = Annotated[
    str | None,
    typer.Option(
        "--model", help="Explicit model; otherwise read committed execution defaults."
    ),
]
EffortOption = Annotated[
    Effort | None,
    typer.Option(
        "--effort",
        help="Reasoning effort; otherwise read committed execution defaults.",
    ),
]


def checked_id(value):
    try:
        return validate_id(value)
    except (ValueError, TypeError) as error:
        raise typer.BadParameter("Use a full lowercase UUIDv4 execution ID.") from error


Identifier = Annotated[
    str, typer.Argument(callback=checked_id, help="Full lowercase execution UUIDv4.")
]


def group(help):
    return typer.Typer(
        help=help,
        no_args_is_help=True,
        add_completion=False,
        rich_markup_mode="rich",
        pretty_exceptions_enable=False,
    )


def command(app, name=None, **options):
    def register(function):
        @wraps(function)
        def guarded(*args, **kwargs):
            try:
                return function(*args, **kwargs)
            except typer.Exit:
                raise
            except (
                OSError,
                ValueError,
                RuntimeError,
                subprocess.SubprocessError,
            ) as error:
                detail = str(error)
                if isinstance(error, subprocess.CalledProcessError) and error.stderr:
                    detail += (
                        "\n"
                        + (
                            error.stderr.decode(errors="replace")
                            if isinstance(error.stderr, bytes)
                            else error.stderr
                        ).strip()
                    )
                display.failure(detail)
                raise typer.Exit(1) from error

        return app.command(name, **options)(guarded)

    return register


def configure(ctx: typer.Context, repo: Repo = Path(".")):
    """Select an exploration repository without running or validating a task."""
    ctx.obj = repo


def repository(ctx):
    return repository_root(ctx.obj)


def usage(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except (ValueError, OSError) as error:
        raise typer.BadParameter(str(error)) from error


def supplied(**values):
    return {
        key: value.value if isinstance(value, Enum) else value
        for key, value in values.items()
        if value is not None
    }


def invocation(repo, group_name, *args):
    parts = ["mutagi", group_name]
    if Path.cwd().resolve() != repo:
        parts += ["--repo", str(repo)]
    return shlex.join([*parts, *map(str, args)])
