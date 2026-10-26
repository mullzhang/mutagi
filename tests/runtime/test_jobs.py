from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import sys
import time

from mutagi.runtime.process import processes


def launch(directory, cwd, seconds, code):
    call(
        directory,
        "start",
        "--cwd",
        str(cwd),
        "--seconds",
        str(seconds),
        "--",
        sys.executable,
        "-c",
        code,
    )


def wait_for(directory, predicate):
    until = time.monotonic() + 12
    while time.monotonic() < until:
        state = json.loads((directory / "state.json").read_text())
        if predicate(state):
            return state

        time.sleep(0.05)

    raise AssertionError(state)


def call(directory, *args, check=True):
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "mutagi",
            "job",
            args[0],
            str(directory),
            *args[1:],
        ],
        capture_output=True,
        text=True,
        check=check,
        timeout=15,
    )


def test_normal_completion_preserves_log(tmp_path):
    # Given a command whose launcher returns immediately.
    directory = tmp_path / "normal"
    launch(directory, tmp_path, 5, "print('saved result', flush=True)")
    # When the independent supervisor finishes.
    state = wait_for(directory, lambda s: s["stage"] == "finished")
    # Then output and exit state are saved, with no managed processes left.
    assert state["result"] == {"exit_code": 0, "timed_out": False}
    assert state["process"] is None
    assert (directory / "command.log").read_text() == "saved result\n"


def test_deadline_stops_separate_group_descendant(tmp_path):
    # Given a command with a child in a separate process group.
    directory = tmp_path / "timeout"
    code = "import subprocess,sys,time; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'],start_new_session=True); print(p.pid,flush=True); time.sleep(30)"
    launch(directory, tmp_path, 2, code)
    # When the deadline expires.
    state = wait_for(directory, lambda s: s["stage"] == "finished")
    # Then both the command and its tracked child terminate.
    child = int((directory / "command.log").read_text())
    assert state["result"]["timed_out"]
    assert child not in processes()
    assert state["process"] is None


def test_explicit_stop_recovers_worker(tmp_path):
    # Given a running job and an active supervisor.
    directory = tmp_path / "stop"
    launch(directory, tmp_path, 15, "import time; time.sleep(30)")
    state = wait_for(directory, lambda s: s["process"] is not None)
    worker = state["process"]["pid"]
    assert call(directory, "recover", check=False).returncode != 0
    # When a stop is requested and the supervisor remains responsible for cleanup.
    call(directory, "stop")
    state = wait_for(directory, lambda s: s["stage"] == "finished")
    # Then the worker exits and cancellation remains distinct from timeout.
    assert state["result"]["cancelled"]
    assert not state["result"]["timed_out"]
    assert worker not in processes()


def test_recovery_after_supervisor_kill_is_idempotent(tmp_path):
    # Given saved ownership of a command with observable output.
    directory = tmp_path / "crash"
    launch(
        directory, tmp_path, 15, "import time; print('once',flush=True); time.sleep(30)"
    )
    state = wait_for(
        directory,
        lambda s: (
            s["process"] is not None and (directory / "command.log").stat().st_size > 0
        ),
    )
    worker = state["process"]["pid"]
    monitor = state["supervisor"]["pid"]
    # When the supervisor is killed and recovery is explicitly requested.
    os.kill(monitor, signal.SIGKILL)
    until = time.monotonic() + 3
    while monitor in processes() and time.monotonic() < until:
        time.sleep(0.05)

    call(directory, "recover")
    # Then the worker is stopped without rerunning, and repeated recovery is a no-op.
    state = json.loads((directory / "state.json").read_text())
    assert state["stage"] == "recovered"
    assert state["process"] is None
    assert worker not in processes()
    before = hashlib.sha256((directory / "state.json").read_bytes()).hexdigest()
    call(directory, "recover")
    assert hashlib.sha256((directory / "state.json").read_bytes()).hexdigest() == before
    assert (directory / "command.log").read_text() == "once\n"


def test_duplicate_start_does_not_overwrite_evidence(tmp_path):
    # Given a completed job with a fixed ID and logs.
    directory = tmp_path / "duplicate"
    launch(directory, tmp_path, 5, "print('original')")
    state = wait_for(directory, lambda s: s["stage"] == "finished")
    # When a second start is requested in the same directory.
    result = call(
        directory,
        "start",
        "--cwd",
        str(tmp_path),
        "--seconds",
        "5",
        "--",
        sys.executable,
        "-c",
        "print('replacement')",
        check=False,
    )
    # Then the request is rejected and the original identity and output are retained.
    assert result.returncode != 0
    assert json.loads((directory / "state.json").read_text())["id"] == state["id"]
    assert (directory / "command.log").read_text() == "original\n"
