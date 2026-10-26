"""Typer service controls; recorded launchd and tick invocations stay unchanged."""

import signal
from pathlib import Path

import typer

from mutagi.commands import display
from mutagi.commands.common import (
    Details,
    Identifier,
    Json,
    Repo,
    command,
    configure,
    group,
    invocation,
    repository,
)
from mutagi.commands.series import start
from mutagi.commands.status import show_service
from mutagi.execution.service import (
    HostShutdown,
    manage,
    request_pause,
    tick as service_tick,
)
from mutagi.execution.state import lock
from mutagi.runtime.launchd import (
    install as install_agent,
    uninstall as uninstall_agent,
)

app = group(
    "Manage recovery of an explicitly assigned series. Status does not imply launchd is running."
)


@app.callback()
def configure_service(ctx: typer.Context, repo: Repo = Path(".")):
    configure(ctx, repo)

    def shutdown(*_):
        raise HostShutdown()

    signal.signal(signal.SIGTERM, shutdown)


app.command(
    "start", help="Create a finite series and assign it to recovery management."
)(start)


@command(app)
def install(ctx: typer.Context, json: Json = False):
    """Install the current Python invocation as a macOS LaunchAgent."""
    repo = repository(ctx)
    result = install_agent(repo)
    if json:
        display.payload(result)
    else:
        display.title("Service installation configured")
        display.fields(list(result.items()))
        display.next_steps(invocation(repo, "service", "status"))


@command(app)
def uninstall(ctx: typer.Context, json: Json = False):
    """Remove the registered LaunchAgent without changing saved execution records."""
    uninstall_agent(repository(ctx))
    if json:
        display.payload({"uninstalled": True})
    else:
        display.title("Service registration removed")
        display.note("Saved series and execution records are retained.")


@command(app, "manage")
def manage_series(ctx: typer.Context, id: Identifier, json: Json = False):
    """Assign an existing series to automatic recovery."""
    repo = repository(ctx)
    with lock(repo):
        manage(repo, id)
    show_service(repo, raw=json)


@command(app)
def pause(ctx: typer.Context, json: Json = False):
    """Request preservation before the service starts further work."""
    repo = repository(ctx)
    request_pause(repo)
    if json:
        display.payload({"pause_requested": True})
    else:
        display.title("Pause requested")
        display.note("The active task will be preserved before further work starts.")
        display.next_steps(invocation(repo, "service", "status"))


@command(app)
def status(ctx: typer.Context, json: Json = False, details: Details = False):
    """Inspect the management checkpoint without changing it."""
    show_service(repository(ctx), raw=json, details=details)


@command(app)
def tick(ctx: typer.Context):
    """Perform one assigned recovery tick (also used by LaunchAgents)."""
    try:
        service_tick(repository(ctx))
    except HostShutdown:
        return
