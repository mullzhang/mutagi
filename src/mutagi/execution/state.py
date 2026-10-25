"""Locate execution checkpoints, enforce ownership, and resolve recovery targets."""

from __future__ import annotations

import fcntl
import json
import os
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID

ROUND_TERMINAL = ("completed", "budget_exhausted", "usage_exhausted", "usage_unknown")

SERIES_TERMINAL = (
    "completed",
    "budget_exhausted",
    "integration_deferred",
    "usage_exhausted",
    "usage_unknown",
)

EXPERIMENT_TERMINAL = ("registered",)


def validate_id(value: str) -> str:
    parsed = UUID(value)
    if parsed.version != 4 or str(parsed) != value:
        raise ValueError("the ID must be a full lowercase UUIDv4")

    return value


@contextmanager
def lock(repo: Path):
    store = repo / ".mutagi/runs"
    store.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (store / "lock").open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("another runner is using this repository") from None

        yield


def service_pause_requested(repo, series_id):
    path = repo / ".mutagi/runs/service.json"
    return (
        series_id is not None
        and (repo / ".mutagi/runs/service.pause").exists()
        and path.exists()
        and json.loads(path.read_text())["id"] == series_id
    )


def require_series_owner(repo, series_id=None):
    for path in (repo / ".mutagi/runs" / "series").glob("*/state.json"):
        state = json.loads(path.read_text())
        if state["phase"] not in SERIES_TERMINAL and state["id"] != series_id:
            raise RuntimeError(
                "resume the unfinished series before starting another run"
            )


def state_path(repo, kind, identifier):
    directory = repo / ".mutagi/runs"
    if kind != "experiment":
        directory /= {"round": "rounds", "series": "series"}[kind]
    return directory / validate_id(identifier) / "state.json"


def load_state(path):
    try:
        state = json.loads(path.read_text())
    except (OSError, ValueError) as error:
        raise ValueError(f"{path}: cannot read saved state: {error}") from error
    if not isinstance(state, dict):
        raise ValueError(f"{path}: expected a saved state object")
    return state


def _recovery_owner(kind, state, rounds):
    if "series_id" in state:
        return "series", state["series_id"]
    if kind == "experiment":
        for owner in rounds:
            if any(item["id"] == state["id"] for item in owner["jobs"]):
                return (
                    ("series", owner["series_id"])
                    if "series_id" in owner
                    else ("round", owner["id"])
                )
    return kind, state["id"]


def resume_owner(repo, kind, state):
    rounds = (
        load_state(path)
        for path in sorted((repo / ".mutagi/runs/rounds").glob("*/state.json"))
    )
    return _recovery_owner(kind, state, rounds)


def unfinished_executions(repo):
    if os.name != "posix":
        raise ValueError("execution-state inspection requires a supported POSIX system")

    groups = {}
    for kind, pattern in (
        ("series", "series/*/state.json"),
        ("round", "rounds/*/state.json"),
        ("experiment", "*/state.json"),
    ):
        groups[kind] = []
        for path in sorted((repo / ".mutagi/runs").glob(pattern)):
            try:
                state = json.loads(path.read_text())
                if not isinstance(state, dict) or not {"id", "phase"} <= state.keys():
                    raise ValueError("expected an execution ID and phase")
                if not isinstance(state["id"], str) or not isinstance(
                    state["phase"], str
                ):
                    raise ValueError("execution ID and phase must be strings")
                validate_id(state["id"])
                if "series_id" in state:
                    if not isinstance(state["series_id"], str):
                        raise ValueError("series_id must be a UUID string")
                    validate_id(state["series_id"])
                if kind == "round" and (
                    not isinstance(state.get("jobs"), list)
                    or any(
                        not isinstance(job, dict)
                        or "id" not in job
                        or not isinstance(job["id"], str)
                        for job in state["jobs"]
                    )
                ):
                    raise ValueError("expected the round's allocated jobs")
            except (ValueError, OSError) as error:
                raise ValueError(
                    f"{path}: cannot inspect execution state: {error}"
                ) from error
            groups[kind].append(state)
    unfinished = []
    for kind, terminal in (
        ("series", SERIES_TERMINAL),
        ("round", ROUND_TERMINAL),
        ("experiment", EXPERIMENT_TERMINAL),
    ):
        for state in groups[kind]:
            if state["phase"] in terminal:
                continue
            unfinished.append(
                {
                    "kind": kind,
                    "state": state,
                    "owner": _recovery_owner(kind, state, groups["round"]),
                }
            )
    return unfinished
