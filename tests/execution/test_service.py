from __future__ import annotations

import json

import pytest

import mutagi.execution.series as series
import mutagi.execution.service as service


def test_managed_service_recovers_checkpoint_and_keeps_operator_stop(
    adaptive_case, monkeypatch
):
    # Given a managed series supervisor interrupted before allocating the first round.
    repo, directory, calls = adaptive_case
    service.manage(repo, directory.name)
    original = series.Series.advance
    monkeypatch.setattr(
        series.Series,
        "advance",
        lambda *a, **k: (_ for _ in ()).throw(service.HostShutdown()),
    )
    with pytest.raises(service.HostShutdown):
        service.tick(repo)

    assert (
        json.loads(service.config_path(repo).read_text())["phase"] == "recovery_pending"
    )
    monkeypatch.setattr(series.Series, "advance", original)
    # When the service resumes, then complete the same ID without repeating on the next tick.
    service.tick(repo)
    service.tick(repo)
    assert len(calls.read_text().splitlines()) == 5
    assert json.loads(service.config_path(repo).read_text())["phase"] == "completed"


def test_managed_pause_survives_service_restart(adaptive_case):
    # Given an operator pause before the first allocation.
    repo, directory, calls = adaptive_case
    service.manage(repo, directory.name)
    (repo / ".mutagi/runs/service.pause").write_text("pause")
    # When launchd invokes the service twice, then no session starts and the pause persists.
    service.tick(repo)
    service.tick(repo)
    assert not calls.exists()
    assert series.Series(directory).state["phase"] == "stopped"
    assert json.loads(service.config_path(repo).read_text())["enabled"] is False


def test_explicit_manage_resumes_operator_pause_with_original_deadline(adaptive_case):
    # Given an operator-paused series that remains paused on automatic startup.
    repo, directory, calls = adaptive_case
    service.manage(repo, directory.name)
    deadline = series.Series(directory).state["deadline"]
    (repo / ".mutagi/runs/service.pause").write_text("pause")
    service.tick(repo)
    assert series.Series(directory).state["phase"] == "stopped"
    # When the operator explicitly manages the same ID again, then continue without extending its deadline.
    service.manage(repo, directory.name)
    service.tick(repo)
    final = series.Series(directory).state
    assert final["phase"] == "completed"
    assert final["deadline"] == deadline
    assert len(calls.read_text().splitlines()) == 5
    assert not (repo / ".mutagi/runs/service.pause").exists()


def test_service_failure_retries_are_bounded(adaptive_case, monkeypatch):
    # Given persistent repository or preservation errors.
    repo, directory, calls = adaptive_case
    service.manage(repo, directory.name)
    monkeypatch.setattr(
        series.Series,
        "advance",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("save unavailable")),
    )
    # When startup repeats, then do not retry the model indefinitely.
    for _ in range(5):
        service.tick(repo)

    config = json.loads(service.config_path(repo).read_text())
    assert config["attempts"] == 3
    assert config["phase"] == "needs_attention"
    assert not config["enabled"]
    assert not calls.exists()


def test_service_recovers_after_real_supervisor_sigkill(adaptive_case):
    # Given a managed supervisor killed after the first round is published.
    import os
    import subprocess
    import sys

    repo, directory, calls = adaptive_case
    service.manage(repo, directory.name)
    setup = "import os;import mutagi.execution.series as run_series;import mutagi.execution.service as series_service;from pathlib import Path\n"
    failure = (
        setup
        + "original=run_series.Series.save\ndef crash(self):\n original(self)\n if self.state['next']==1:os.kill(os.getpid(),9)\nrun_series.Series.save=crash\n"
        + f"series_service.tick(Path({str(repo)!r}))\n"
    )
    stopped = subprocess.run(
        [sys.executable, "-c", failure],
        env=os.environ,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert stopped.returncode == -9, stopped.stderr
    assert len(calls.read_text().splitlines()) == 3
    before = series.Series(directory).state
    # When a new process performs scheduled recovery, then run only the remaining rounds.
    resumed = subprocess.run(
        [sys.executable, "-c", setup + f"series_service.tick(Path({str(repo)!r}))\n"],
        env=os.environ,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert resumed.returncode == 0, resumed.stderr
    after = series.Series(directory).state
    assert after["phase"] == "completed"
    assert after["deadline"] == before["deadline"]
    assert len(calls.read_text().splitlines()) == 5
    assert json.loads(service.config_path(repo).read_text())["attempts"] == 2
