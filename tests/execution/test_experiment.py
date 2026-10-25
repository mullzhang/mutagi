from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

import mutagi.execution.experiment as experiment
import mutagi.execution.state as execution_state
import mutagi.project.repository as repository
from tests.support.execution import create


def test_uses_project_paths_and_preserves_start_configuration(
    repo, tmp_path, fake_codex
):
    # Given a project with implementation, experiment, and catalog paths different from naedo.
    (repo / "experiments").rename(repo / "trials")
    config_path = repo / ".mutagi/config.toml"
    text = (
        config_path.read_text()
        .replace('"experiments"', '"trials"')
        .replace('"experiments/INDEX.md"', '"trials/INDEX.md"')
        .replace('"naedo/"', '"src/"')
    )
    config_path.write_text(text)
    repository.git(repo, "add", ".")
    repository.git(repo, "commit", "-m", "test: configure different project layout")
    fake_codex.write_text(
        fake_codex.read_text().replace(
            "pathlib.Path('experiments')", "pathlib.Path('trials')"
        )
    )

    # When settings and scope are checked and exploration, preservation, and registration complete.
    job = create(repo, tmp_path)
    assert job.project["experiments"] == "trials"
    assert job.allowed("src/app.py", job.directory())
    assert not job.allowed("naedo/app.py", job.directory())
    job.advance()

    # Then records use only the configured paths and run state retains the base settings.
    assert job.state["phase"] == "registered"
    assert job.state["result"]["record"].startswith("trials/")
    assert job.state["id"] in (repo / "trials/INDEX.md").read_text()
    assert not (repo / "experiments").exists()
    config_path.write_text(text.replace('"src/"', '"elsewhere/"'))
    restored = experiment.Runner(job.run)
    assert restored.allowed("src/app.py", restored.directory())
    assert not restored.allowed("elsewhere/app.py", restored.directory())


def test_normal_run_preserves_binary_input_and_registers_once(
    repo, tmp_path, fake_codex
):
    # Given a completed experiment with a fixed implementation and untracked evidence.
    job = create(repo, tmp_path)
    # When the full runner and its idempotent recovery execute.
    result = job.advance()
    head = repository.git(repo, "rev-parse", "HEAD")
    job.advance(recovery=True)
    # Then evidence is saved on its dedicated branch and referenced by one catalog commit.
    assert result["phase"] == "registered"
    assert result["reason"] == "completed"
    assert repository.git(repo, "rev-parse", "HEAD") == head
    index = (repo / "experiments/INDEX.md").read_text()
    assert index.count(f"<!-- forest-run:{result['id']} -->") == 1
    record = result["result"]["record"]
    assert repository.git(repo, "show", result["saved_commit"] + ":" + record)
    evidence = str(Path(record).parent / "necessary.txt")
    assert (
        repository.git(repo, "show", result["saved_commit"] + ":" + evidence)
        == "untracked evidence"
    )
    assert not (repo / Path(record).parent).exists()
    assert job.state["thread_id"] is not None
    assert not repository.git(repo, "status", "--porcelain")


def test_pending_run_prevents_next_experiment(repo, tmp_path):
    create(repo, tmp_path)
    with pytest.raises(RuntimeError, match="unfinished run"):
        create(repo, tmp_path)


def test_expired_execution_recovers_without_model_call(repo, tmp_path, monkeypatch):
    job = create(repo, tmp_path)
    job.prepare()
    job.state.update(phase="executing", exploration_deadline=0, deadline=0)
    job.save()
    monkeypatch.setattr(
        experiment.Runner, "execute", lambda _: pytest.fail("Agent restarted")
    )
    job.advance(recovery=True)
    assert job.state["reason"] == "runner_interrupted"
    assert job.state["result"]["status"] == "unresolved"
    assert job.state["phase"] == "registered"


def test_preparation_failure_registered_as_blocked(repo, tmp_path, monkeypatch):
    job = create(repo, tmp_path)
    job.project["preparation"] = [[sys.executable, "-c", "raise SystemExit(1)"]]
    job.save()
    job.advance()
    assert job.state["phase"] == "registered"
    assert job.state["result"]["status"] == "blocked"
    assert job.state["thread_id"] is None


def test_agent_timeout_is_unresolved_and_registered(repo, tmp_path, fake_codex):
    fake_codex.write_text(f"#!{sys.executable}\nimport time\ntime.sleep(60)\n")
    job = create(repo, tmp_path, seconds=20, reserve=10)
    job.prepare()
    job.state["exploration_deadline"] = time.time() + 0.2
    job.save()
    job.advance()
    assert job.state["reason"] == "time_limit"
    assert job.state["phase"] == "registered"
    assert job.state["result"]["status"] == "unresolved"


def test_repository_lock_prevents_parallel_launch(repo):
    with execution_state.lock(repo):
        with pytest.raises(RuntimeError, match="another runner"):
            with execution_state.lock(repo):
                pytest.fail("Concurrent controller acquired lock")


def test_inherited_run_requires_record_at_fixed_base(repo, tmp_path):
    from uuid import uuid4

    parent = str(uuid4())
    record = repo / "experiments/parent.md"
    record.write_text(f"---\nid: {parent}\n---\n")
    repository.git(repo, "add", ".")
    repository.git(repo, "commit", "-m", "docs: parent record")
    job = create(
        repo, tmp_path, parent_id=parent, parent_record="experiments/parent.md"
    )
    assert job.state["parent_ids"] == [parent]
    assert job.state["base"] == repository.git(repo, "rev-parse", "HEAD")
