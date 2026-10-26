from __future__ import annotations

import json
import re
import sys
import time

from tests.support.cli import invoke

import pytest

import mutagi.commands.experiment as experiment_command
import mutagi.commands.job as job_command
import mutagi.commands.round as round_command
import mutagi.commands.series as series_command
import mutagi.commands.service as service_command
import mutagi.commands.status as status
import mutagi.execution.experiment as experiment
import mutagi.execution.round as round_controller
import mutagi.execution.series as series
import mutagi.execution.service as service
import mutagi.execution.state as execution_state
import mutagi.runtime.jobs as jobs
import mutagi.runtime.storage as storage
from tests.support.plans import plan_for
from tests.support.repositories import snapshot


@pytest.mark.parametrize(
    "kind,module,creator",
    [
        ("round", round_command, round_controller.create_round),
        ("series", series_command, series.create_series),
    ],
)
def test_status_shows_progress_and_preserves_raw_state(
    repo, monkeypatch, capsys, kind, module, creator
):
    plan = {**plan_for(kind), "model": "chosen-model"}
    directory = creator(repo, plan)
    before = snapshot(repo)
    command = [f"mutagi {kind}", "--repo", str(repo), "status", directory.name]
    monkeypatch.setattr(sys, "argv", command)
    invoke(module)
    output = capsys.readouterr().out
    assert re.search(r"Recorded phase\s+running", output)
    assert re.search(r"Recorded progress\s+0/", output)
    assert "Human adoption: not tracked" in output
    assert f"resume {directory.name}" in " ".join(output.split())
    monkeypatch.setattr(sys, "argv", [*command, "--json"])
    invoke(module)
    assert capsys.readouterr().out == (directory / "state.json").read_text()
    assert snapshot(repo) == before


def test_experiment_status_distinguishes_observation_preservation_and_adoption(
    repo, monkeypatch, capsys
):
    with execution_state.lock(repo):
        directory = experiment.create(
            repo, "HEAD", "case", "chosen-model", "medium", 60, 10
        )
    state = json.loads((directory / "state.json").read_text())
    state.update(
        phase="registered",
        saved_commit=state["base"],
        result={
            "status": "refuted",
            "summary": "Counterexample",
            "limits": "Finite inputs",
            "record": "experiments/one/record.md",
        },
    )
    storage.atomic_json(directory / "state.json", state)
    before = snapshot(repo)
    monkeypatch.setattr(
        sys,
        "argv",
        ["mutagi experiment", "--repo", str(repo), "status", directory.name],
    )
    invoke(experiment_command)
    output = capsys.readouterr().out
    assert re.search(r"Recorded phase\s+registered", output)
    assert re.search(r"Reported observation\s+refuted", output)
    assert "Saved revision" in output
    assert "Human adoption: not tracked" in output
    assert "git -C" not in output
    status.show_execution(repo, "experiment", directory.name, details=True)
    assert "git -C" in capsys.readouterr().out
    monkeypatch.setattr(
        sys,
        "argv",
        ["mutagi experiment", "--repo", str(repo), "status", directory.name, "--json"],
    )
    invoke(experiment_command)
    assert json.loads(capsys.readouterr().out) == state
    assert snapshot(repo) == before


def test_nested_status_uses_owner_for_recovery(repo, capsys):
    plan = {**plan_for("round"), "model": "chosen"}
    directory = round_controller.create_round(repo, plan)
    state = json.loads((directory / "state.json").read_text())
    child_id = state["jobs"][0]["id"]
    with execution_state.lock(repo):
        experiment.create(
            repo, "HEAD", "case", "chosen", "medium", 60, 10, identifier=child_id
        )
    status.show_execution(repo, "experiment", child_id)
    output = capsys.readouterr().out
    assert f"mutagi round --repo {repo} resume {directory.name}" in " ".join(
        output.split()
    )
    assert f"experiment --repo {repo} resume" not in output
    status.show_execution(repo, "round", directory.name)
    assert "Current child" in capsys.readouterr().out


def test_process_observation_checks_identity_and_system_boot(monkeypatch):
    process = {
        "pid": 42,
        "identity": "original",
        "boot_identity": "boot",
        "children": [{"pid": 43, "identity": "child"}],
    }
    monkeypatch.setattr(status, "boot_identity", lambda: "boot")
    monkeypatch.setattr(
        status,
        "processes",
        lambda: {42: {"identity": "reused"}, 43: {"identity": "child"}},
    )
    assert (
        status.observed_processes({"process": process})
        == "Recorded managed processes are present: 43"
    )
    monkeypatch.setattr(status, "boot_identity", lambda: "new-boot")
    assert "earlier system boot" in status.observed_processes({"process": process})


def test_job_status_read_only_and_raw_json(repo, monkeypatch, capsys):
    directory = repo / ".mutagi/runs/job-test"
    directory.mkdir(parents=True)
    state = {
        "id": "job-id",
        "cwd": str(repo),
        "command": ["echo", "hello"],
        "stage": "finished",
        "deadline": time.time(),
        "process": None,
        "supervisor": None,
        "result": {"exit_code": 0, "timed_out": False},
    }
    jobs.save_state(directory, state)
    before = snapshot(repo)
    monkeypatch.setattr(jobs, "processes", lambda: {})
    monkeypatch.setattr(sys, "argv", ["mutagi job", "status", str(directory)])
    invoke(job_command)
    output = capsys.readouterr().out
    assert re.search(r"Recorded stage\s+finished", output)
    assert "Command log" not in output
    status.show_job(directory, jobs.observe(dict(state)), details=True)
    assert "Command log" in capsys.readouterr().out
    monkeypatch.setattr(sys, "argv", ["mutagi job", "status", str(directory), "--json"])
    invoke(job_command)
    assert json.loads(capsys.readouterr().out) == state
    assert snapshot(repo) == before


def test_service_status_preserves_service_and_series_checkpoints(
    repo, monkeypatch, capsys
):
    plan = {**plan_for("series"), "model": "chosen"}
    directory = series.create_series(repo, plan)
    service.manage(repo, directory.name)
    before = snapshot(repo)
    command = ["mutagi service", "--repo", str(repo), "status"]
    monkeypatch.setattr(sys, "argv", command)
    invoke(service_command)
    output = capsys.readouterr().out
    assert "Service · pending" in output
    assert "Recorded progress" in output
    assert "does not prove" in output
    monkeypatch.setattr(sys, "argv", [*command, "--json"])
    invoke(service_command)
    assert json.loads(capsys.readouterr().out) == json.loads(
        service.config_path(repo).read_text()
    )
    assert snapshot(repo) == before


def test_app_handoff_status_does_not_suggest_cli_resume(repo, capsys):
    with execution_state.lock(repo):
        directory = experiment.create(repo, "HEAD", "case", "chosen", "medium", 60, 10)
    state = json.loads((directory / "state.json").read_text())
    state.update(phase="awaiting_app", thread_id="assigned-app-thread")
    storage.atomic_json(directory / "state.json", state)
    before = snapshot(repo)
    status.show_execution(repo, "experiment", directory.name)
    output = capsys.readouterr().out
    assert re.search(r"Assigned App thread\s+assigned-app-thread", output)
    assert "mutagi handoff" in output and "--app-observation" in output
    assert "resume" not in output
    assert snapshot(repo) == before
