from __future__ import annotations

import copy
import io
import os
import subprocess
import sys
import time

import pytest

import mutagi.runtime.process as process_control
from mutagi.runtime.process import recover_process, run_job


def test_deadline_stops_child_processes(tmp_path):
    # Given a waiting parent and a child in the same process group that ignores TERM.
    child = tmp_path / "child.pid"
    program = f"""import subprocess,sys,time
p=subprocess.Popen([sys.executable,'-c','import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(60)'])
open({str(child)!r},'w').write(str(p.pid))
time.sleep(60)
"""
    state = {}
    # When the deadline expires, terminate the group rather than only its leader.
    outcome = run_job(
        [sys.executable, "-c", program],
        tmp_path,
        tmp_path / "job.log",
        time.time() + 0.5,
        state,
        lambda: None,
    )
    assert outcome["timed_out"]
    pid = int(child.read_text())
    rows = subprocess.check_output(["ps", "-axo", "pid=,stat="], text=True)
    assert not any(
        int(p) == pid and not status.startswith("Z")
        for p, status in (r.split() for r in rows.splitlines())
    )
    assert state["process"] is None


def test_success_also_stops_background_children(tmp_path):
    state = {}
    program = 'import subprocess,sys; subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"])'
    assert (
        run_job(
            [sys.executable, "-c", program],
            tmp_path,
            tmp_path / "job.log",
            time.time() + 10,
            state,
            lambda: None,
        )["exit_code"]
        == 0
    )
    assert state["process"] is None


def test_recover_pid_reuse_refuses_unrelated_process():
    state = {
        "process": {"pid": os.getpid(), "identity": "not this process", "children": []}
    }
    with pytest.raises(RuntimeError, match="identity changed"):
        recover_process(state, lambda: None)


def test_interrupted_runner_recovers_its_live_process(repo, tmp_path):
    # Given an interrupted controller with saved ownership of a running process.
    from mutagi.runtime.process import identity

    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True
    )
    state = {
        "process": {
            "pid": process.pid,
            "identity": identity(process.pid),
            "children": [],
        }
    }
    try:
        recover_process(state, lambda: None)
        process.wait(timeout=2)
        assert state["process"] is None
    finally:
        if process.poll() is None:
            os.killpg(process.pid, 9)
            process.wait()


def test_stdin_backpressure_cannot_extend_deadline(tmp_path):
    # Given a child that does not read a prompt larger than pipe capacity.
    state = {"process": None}
    started = time.monotonic()
    # When the job deadline expires during input transfer.
    outcome = run_job(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        tmp_path,
        tmp_path / "job.log",
        time.time() + 0.2,
        state,
        lambda: None,
        stdin=io.StringIO("x" * 2_000_000),
    )
    # Then input transfer is also bounded by the deadline and managed processes stop.
    assert outcome["timed_out"]
    assert time.monotonic() - started < 6
    assert state["process"] is None


@pytest.mark.parametrize("timeout", [False, True])
def test_child_in_its_own_session_is_stopped(tmp_path, timeout):
    # Given a child in a new session, as used by Codex shell commands.
    from mutagi.runtime.process import processes

    child_file = tmp_path / "child.pid"
    script = (
        "import subprocess,sys,time,pathlib; "
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],start_new_session=True); "
        f"pathlib.Path({str(child_file)!r}).write_text(str(p.pid)); "
        f"time.sleep({60 if timeout else 0.7})"
    )
    state = {"process": None}
    saved_children = []
    # When the parent completes or reaches its deadline.
    result = run_job(
        [sys.executable, "-c", script],
        tmp_path,
        tmp_path / "process.log",
        time.time() + (1.0 if timeout else 5.0),
        state,
        lambda: saved_children.extend(
            state["process"]["children"] if state["process"] else []
        ),
    )
    # Then the separate process group is durably tracked and stopped.
    child = int(child_file.read_text())
    assert result["timed_out"] == timeout
    assert any(item["pid"] == child for item in saved_children)
    assert child not in processes()


def test_recovery_stops_recorded_child_after_parent_exited(tmp_path):
    # Given saved ownership of a separate-session child after the controller's parent exits.
    from mutagi.runtime.process import identity

    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True
    )
    parent = subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True)
    parent.wait()
    state = {
        "process": {
            "pid": parent.pid,
            "identity": "exited",
            "children": [{"pid": child.pid, "identity": identity(child.pid)}],
        }
    }
    try:
        # When recovery uses saved identity to operate across groups.
        recover_process(state, lambda: None)
        child.wait(timeout=2)
        assert state["process"] is None
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()


def test_identity_failure_reaps_unreleased_gate_and_preserves_error(
    tmp_path, monkeypatch
):

    # Given process inspection denied before the command receives permission to execute.
    children = []
    original_popen = process_control.subprocess.Popen

    def capture_child(*args, **kwargs):
        process = original_popen(*args, **kwargs)
        children.append(process)
        return process

    def denied_identity(_):
        raise PermissionError("process inspection denied")

    monkeypatch.setattr(process_control.subprocess, "Popen", capture_child)
    monkeypatch.setattr(process_control, "identity", denied_identity)
    marker = tmp_path / "must-not-exist"
    state = {"process": None}
    # When ownership cannot be recorded, retain the original failure.
    with pytest.raises(PermissionError, match="process inspection denied"):
        run_job(
            [sys.executable, "-c", f"open({str(marker)!r}, 'w').close()"],
            tmp_path,
            tmp_path / "job.log",
            time.time() + 10,
            state,
            lambda: None,
        )

    # Then recover the start gate, leaving neither a command nor stale ownership.
    assert len(children) == 1
    assert children[0].returncode == 125
    assert not marker.exists()
    assert state["process"] is None


def test_prior_boot_processes_are_not_signalled(monkeypatch):
    # Given ownership from a previous OS boot with a reused PID.
    state = {
        "process": {
            "pid": 123,
            "identity": "old",
            "children": [],
            "boot_identity": "old-boot",
        }
    }
    monkeypatch.setattr(process_control, "boot_identity", lambda: "new-boot")
    monkeypatch.setattr(
        process_control,
        "stop",
        lambda *_: pytest.fail("Must not signal a previous boot PID"),
    )
    saved = []
    # When recovering, then clear stale ownership without touching current processes.
    process_control.recover_process(state, lambda: saved.append(copy.deepcopy(state)))
    assert state["process"] is None
    assert saved == [{"process": None}]
