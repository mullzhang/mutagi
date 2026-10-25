from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

import mutagi.execution.experiment as experiment
import mutagi.project.repository as repository
import mutagi.runtime.storage as storage
from tests.support.execution import create


@pytest.fixture
def app_ready(repo, tmp_path, fake_codex):
    import hashlib

    job = create(repo, tmp_path)
    job.prepare()
    job.execute()  # Generate the fixture only; do not execute the handoff itself.
    (job.run / "events.jsonl").unlink()
    log = job.directory() / "worker.log"
    log.write_text("App handoff fixture\n")
    (repo / ".git/info/exclude").write_text("*.log\n")
    paths = set(job.changed()) | {log.relative_to(job.worktree).as_posix()}
    receipt = {
        "id": job.state["id"],
        "thread_id": "app-test-chat",
        "files": {
            p: hashlib.sha256((job.worktree / p).read_bytes()).hexdigest()
            for p in paths
        },
    }
    storage.atomic_json(job.run / "receipt.json", receipt)
    job.state.update(phase="awaiting_app", thread_id=receipt["thread_id"], reason=None)
    job.save()
    observation = {
        "thread_id": receipt["thread_id"],
        "thread_status": "idle",
        "turn_status": "completed",
        "turn_id": "app-test-turn",
        "observed_at": storage.utc(time.time()),
    }
    return job, receipt, observation


def test_app_handoff_preserves_ignored_log_and_registers_once(app_ready, monkeypatch):
    from mutagi.execution.handoff import accept

    # Given App output with required ignored logs and a turn observed to completion.
    job, receipt, observation = app_ready
    monkeypatch.setattr(
        experiment.Runner, "execute", lambda _: pytest.fail("Agent restarted")
    )
    # When the controller accepts the output twice.
    first = accept(job.run, observation)
    second = accept(job.run, observation)
    # Then ignored logs are committed exactly, with one registration and no new agent.
    assert first["saved_commit"] == second["saved_commit"]
    assert first["registry_commit"] == second["registry_commit"]
    assert first["phase"] == "registered"
    for path in receipt["files"]:
        assert (
            subprocess.check_output(
                ["git", "-C", str(job.repo), "show", first["saved_commit"] + ":" + path]
            )
            == (job.worktree / path).read_bytes()
        )

    record = job.directory().relative_to(job.worktree) / "_runner/app-handoff.json"
    stored = json.loads(
        repository.git(job.repo, "show", first["saved_commit"] + ":" + str(record))
    )
    assert stored["receipt"] == receipt
    assert stored["app_observation"] == observation


@pytest.mark.parametrize("defect", ["active", "omitted_log", "modified_input"])
def test_app_handoff_rejects_unready_or_incomplete_evidence(app_ready, defect):
    from mutagi.execution.handoff import accept

    # Given a nonterminal turn, missing ignored evidence, or altered evidence contents.
    job, receipt, observation = app_ready
    if defect == "active":
        observation["thread_status"] = "running"
    elif defect == "omitted_log":
        receipt["files"] = {
            p: h for p, h in receipt["files"].items() if not p.endswith(".log")
        }
        storage.atomic_json(job.run / "receipt.json", receipt)
    else:
        (job.directory() / "input.bin").write_bytes(b"changed")

    # When handoff is attempted, reject it before preservation and registration.
    with pytest.raises(ValueError):
        accept(job.run, observation)

    # Then remain awaiting the App without registering in the shared catalog.
    assert experiment.Runner(job.run).state["phase"] == "awaiting_app"
    assert job.state["id"] not in (job.repo / "experiments/INDEX.md").read_text()


def test_app_handoff_resumes_failed_commit_with_frozen_receipt(app_ready):
    from mutagi.execution.handoff import accept

    # Given a rejected local preservation commit for a completed App turn.
    job, receipt, observation = app_ready
    hook = job.repo / ".git/hooks/pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    with pytest.raises(RuntimeError, match="preservation failed"):
        accept(job.run, observation)

    assert experiment.Runner(job.run).state["phase"] == "saving"
    hook.unlink()
    # When the declared inventory changes, reject recovery before registration.
    changed = dict(receipt, thread_id="another-chat")
    storage.atomic_json(job.run / "receipt.json", changed)
    with pytest.raises(ValueError, match="identity"):
        accept(job.run, observation)

    storage.atomic_json(job.run / "receipt.json", receipt)
    # Then restoring the accepted receipt allows recovery with the same ID.
    result = accept(job.run, observation)
    assert result["id"] == receipt["id"]
    assert result["phase"] == "registered"


@pytest.mark.parametrize("boundary", ["collecting", "saving", "saved", "registered"])
def test_app_handoff_recovers_launcher_death_before_checkpoint(
    app_ready, tmp_path, monkeypatch, boundary
):
    from mutagi.execution.handoff import accept

    # Given completed App evidence and a controller exiting before writing its checkpoint.
    job, receipt, observation = app_ready
    assigned = {key: job.state[key] for key in ("id", "started_at", "deadline")}
    observation_path = tmp_path / "observation.json"
    storage.atomic_json(observation_path, observation)
    program = """import json, os, signal, sys
from pathlib import Path
import mutagi.execution.experiment as runner
from mutagi.execution.handoff import accept
original = runner.Runner.save
def checkpoint(self):
    if self.state['phase'] == sys.argv[3]:
        os.kill(os.getpid(), signal.SIGKILL)
    original(self)
runner.Runner.save = checkpoint
accept(Path(sys.argv[1]), json.loads(Path(sys.argv[2]).read_text()))
"""
    process = subprocess.run(
        [sys.executable, "-c", program, str(job.run), str(observation_path), boundary],
        cwd=Path(experiment.__file__).parent,
        capture_output=True,
        timeout=30,
    )
    assert process.returncode == -9, process.stderr.decode()
    monkeypatch.setattr(
        experiment.Runner, "execute", lambda _: pytest.fail("Agent restarted")
    )

    # When a new controller checks unfinished runs, it does not start another.
    with pytest.raises(RuntimeError, match="unfinished run|clean the working tree"):
        create(job.repo, tmp_path)

    recovered = accept(job.run, observation)
    repeated = accept(job.run, observation)

    # Then recovery preserves identity and all contents, with exactly one registration.
    assert {key: recovered[key] for key in assigned} == assigned
    assert recovered["phase"] == "registered"
    assert recovered["saved_commit"] == repeated["saved_commit"]
    assert recovered["registry_commit"] == repeated["registry_commit"]
    assert (job.repo / "experiments/INDEX.md").read_text().count(
        f"<!-- forest-run:{assigned['id']} -->"
    ) == 1
    for path in receipt["files"]:
        assert (
            subprocess.check_output(
                [
                    "git",
                    "-C",
                    str(job.repo),
                    "show",
                    recovered["saved_commit"] + ":" + path,
                ]
            )
            == (job.worktree / path).read_bytes()
        )

    assert not repository.git(job.repo, "status", "--porcelain")
    assert not repository.git(job.worktree, "status", "--porcelain")
