"""Run one command with a deadline under a detached, recoverable POSIX supervisor."""

from __future__ import annotations

import argparse
import fcntl
import json
import math
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

from mutagi.runtime.process import identity, processes, recover_process, run_job


def read_state(directory):
    return json.loads((directory / "state.json").read_text())


def save_state(directory, state):
    temporary = directory / "state.json.tmp"
    with temporary.open("w") as stream:
        json.dump(state, stream, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())

    temporary.replace(directory / "state.json")


def acquire(directory, *, wait=False):
    stream = (directory / "lock").open("a")
    try:
        fcntl.flock(stream, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
    except BlockingIOError:
        stream.close()
        raise RuntimeError(
            "the supervisor is running; request a stop and wait"
        ) from None

    return stream


def start(directory, cwd, seconds, command):
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("seconds must be positive and finite")

    if not command or not cwd.is_dir():
        raise ValueError("a command and an existing working directory are required")

    directory.mkdir(parents=True, exist_ok=False)
    with acquire(directory):
        state = {
            "id": str(uuid4()),
            "cwd": str(cwd),
            "command": command,
            "started_at": time.time(),
            "deadline": time.time() + seconds,
            "stage": "starting",
            "process": None,
            "supervisor": None,
            "result": None,
        }
        save_state(directory, state)
        with (directory / "supervisor.log").open("wb") as log:
            monitor = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "mutagi.runtime.jobs",
                    "_supervise",
                    str(directory),
                ],
                stdin=subprocess.PIPE,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            try:
                state["supervisor"] = {
                    "pid": monitor.pid,
                    "identity": identity(monitor.pid),
                }
                if state["supervisor"]["identity"] is None:
                    raise RuntimeError("cannot identify the supervisor")

                save_state(directory, state)
                monitor.stdin.write(b"1")
                monitor.stdin.flush()
            finally:
                monitor.stdin.close()

        return state


def supervise(directory):
    # The command cannot start until the controller persists supervisor ownership.
    if sys.stdin.buffer.read(1) != b"1":
        return

    with acquire(directory, wait=True):
        state = read_state(directory)
        state["stage"] = "running"

        def save():
            save_state(directory, state)

        def interrupted(_signum, _frame):
            raise KeyboardInterrupt

        signal.signal(signal.SIGTERM, interrupted)
        signal.signal(signal.SIGINT, interrupted)
        try:
            save()
            state["result"] = run_job(
                state["command"],
                Path(state["cwd"]),
                directory / "command.log",
                state["deadline"],
                state,
                save,
                cancel_requested=lambda: (directory / "stop-requested").exists(),
            )
            state["stage"] = "finished"
        except (Exception, KeyboardInterrupt) as error:
            state["stage"] = "needs_recovery"
            state["error"] = f"{type(error).__name__}: {error}"

        save()


def recover(directory):
    with acquire(directory):
        state = read_state(directory)
        monitor = state["supervisor"]
        live = processes()
        if (
            monitor is not None
            and monitor["pid"] in live
            and live[monitor["pid"]]["identity"] == monitor["identity"]
        ):
            raise RuntimeError("the supervisor still exists; do not race with startup")

        if state["stage"] in ("finished", "recovered"):
            return state

        recover_process(state, lambda: save_state(directory, state))
        state["stage"] = "recovered"
        state["recovered_at"] = time.time()
        # Recovery stops remaining processes without rerunning the command.
        save_state(directory, state)
        return state


def observe(state):
    monitor = state["supervisor"]
    live = processes()
    state["supervisor_alive"] = (
        monitor is not None
        and monitor["pid"] in live
        and live[monitor["pid"]]["identity"] == monitor["identity"]
    )
    return state


def request_stop(directory):
    (directory / "stop-requested").touch(exist_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Supervise one recorded command job.")
    parser.add_argument("action", choices=("_supervise",))
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    supervise(args.directory.resolve())
