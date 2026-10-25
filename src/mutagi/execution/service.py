"""Recover explicitly managed series after a process restart or login."""

from __future__ import annotations

import json
import subprocess
import time

from mutagi.execution.series import Series
from mutagi.execution.state import SERIES_TERMINAL as TERMINAL
from mutagi.execution.state import lock, validate_id
from mutagi.runtime.storage import atomic_json, utc


class HostShutdown(BaseException):
    """Distinguish interruption from a persistent operator pause request."""


def config_path(repo):
    return repo / ".mutagi/runs/service.json"


def manage(repo, identifier):
    directory = repo / ".mutagi/runs/series" / validate_id(identifier)
    state = json.loads((directory / "state.json").read_text())
    if state["repo"] != str(repo) or state["phase"] in TERMINAL:
        raise ValueError("only an unfinished series in this repository can be managed")

    path = config_path(repo)
    if path.exists():
        prior = json.loads(path.read_text())
        if prior["id"] != identifier:
            previous = Series(repo / ".mutagi/runs/series" / prior["id"])
            if previous.state["phase"] not in TERMINAL:
                raise RuntimeError("another managed series is unfinished")

    pause = repo / ".mutagi/runs/service.pause"
    if pause.exists():
        pause.unlink()

    if state["phase"] == "stopped":
        state["phase"] = "running"
        atomic_json(directory / "state.json", state)

    atomic_json(
        path,
        {
            "id": identifier,
            "enabled": True,
            "attempts": 0,
            "phase": "pending",
            "updated_at": utc(time.time()),
        },
    )


def tick(repo):
    path = config_path(repo)
    if not path.exists():
        return

    with lock(repo):
        config = json.loads(path.read_text())
        if not config["enabled"]:
            return

        controller = Series(repo / ".mutagi/runs/series" / validate_id(config["id"]))
        if (
            controller.state["phase"] in TERMINAL
            or controller.state["phase"] == "stopped"
        ):
            config.update(
                enabled=False,
                phase=controller.state["phase"],
                updated_at=utc(time.time()),
            )
            atomic_json(path, config)
            return

        # Retry service failures while retaining the original ID, model deadline,
        # and preservation checkpoint, up to three attempts. Never restart the model.
        if config["attempts"] >= 3:
            config.update(
                enabled=False, phase="needs_attention", updated_at=utc(time.time())
            )
            atomic_json(path, config)
            return

        config.update(
            attempts=config["attempts"] + 1,
            phase="running",
            updated_at=utc(time.time()),
        )
        atomic_json(path, config)
        try:
            result = controller.advance(recovery=True)
            config.update(
                enabled=False, phase=result["phase"], updated_at=utc(time.time())
            )
        except HostShutdown:
            config.update(phase="recovery_pending", updated_at=utc(time.time()))
            atomic_json(path, config)
            raise
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
            config.update(
                phase="recovery_pending", error=str(error), updated_at=utc(time.time())
            )
        finally:
            atomic_json(path, config)


def request_pause(repo):
    # The controller checks this request while it holds the repository lock.
    (repo / ".mutagi/runs/service.pause").write_text("operator requested a pause\n")
