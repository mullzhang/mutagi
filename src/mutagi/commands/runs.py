"""Command-side execution reporting without changing controller ownership."""

import signal
import subprocess

import typer

from mutagi.commands import display
from mutagi.commands.common import invocation
from mutagi.commands.status import show_execution


def interruptible(kind):
    def interrupted(*_):
        raise KeyboardInterrupt(kind + " interrupted")

    signal.signal(signal.SIGTERM, interrupted)


def advance(repo, kind, controller, *, recovery, machine):
    if machine:
        display.note(
            f"{kind.capitalize()} started: {controller.state['id']}", error=True
        )
    else:
        display.title(f"{kind.capitalize()} · {controller.state['id'][:8]}")
        display.fields([("State", "Recovering saved work" if recovery else "Starting")])
    try:
        result = controller.advance(recovery=recovery)
    except (
        OSError,
        RuntimeError,
        ValueError,
        subprocess.SubprocessError,
        KeyboardInterrupt,
    ) as error:
        controller.state["error"] = str(error)
        controller.save()
        display.failure("Work is incomplete. " + str(error))
        display.note(
            "Recovery: " + invocation(repo, kind, "resume", controller.state["id"]),
            error=True,
        )
        raise typer.Exit(1) from error
    if machine:
        display.payload(
            {
                "id": result["id"],
                "phase": result["phase"],
                "reason": result["reason"],
                "saved_commit": result.get("saved_commit"),
            }
            if kind == "experiment"
            else result
        )
    else:
        show_execution(repo, kind, result["id"])
