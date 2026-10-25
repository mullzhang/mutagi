from __future__ import annotations

import json

import pytest

import mutagi.execution.experiment as experiment
import mutagi.project.repository as repository
from tests.support.execution import create


@pytest.mark.parametrize("phase", ["collecting", "saving", "saved"])
def test_resume_after_each_preservation_boundary(
    repo, tmp_path, fake_codex, monkeypatch, phase
):
    # Given a returned agent and a controller interrupted at a phase boundary.
    job = create(repo, tmp_path)
    job.prepare()
    job.execute()
    if phase in ("saving", "saved"):
        job.collect()

    if phase == "saved":
        job.preserve()

    started = job.state["started_at"]
    deadline = job.state["deadline"]
    monkeypatch.setattr(
        experiment.Runner, "execute", lambda _: pytest.fail("Agent restarted")
    )
    # When recovery is invoked twice.
    resumed = experiment.Runner(job.run)
    resumed.advance(recovery=True)
    saved = resumed.state["saved_commit"]
    resumed.advance(recovery=True)
    # Then identity, budget, and saved revision are retained without repeating execution.
    assert resumed.state["saved_commit"] == saved
    assert resumed.state["started_at"] == started
    assert resumed.state["deadline"] == deadline
    assert resumed.state["phase"] == "registered"


def test_git_save_failure_keeps_recovery_and_can_resume(repo, tmp_path, fake_codex):
    job = create(repo, tmp_path)
    job.prepare()
    job.execute()
    job.collect()
    hook = repo / ".git/hooks/pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    # When a commit is rejected, then the archive exists without reporting successful registration.
    with pytest.raises(RuntimeError, match="preservation failed"):
        job.preserve()

    assert job.state["phase"] == "saving"
    assert list((job.run / "recovery").glob("*/hashes.json"))
    assert job.state["id"] not in (repo / "experiments/INDEX.md").read_text()
    hook.unlink()
    # Then later recovery can complete the same run.
    experiment.Runner(job.run).advance(recovery=True)
    assert json.loads((job.run / "state.json").read_text())["phase"] == "registered"


def test_registration_commit_failure_does_not_duplicate(
    repo, tmp_path, fake_codex, monkeypatch
):
    job = create(repo, tmp_path)
    job.prepare()
    job.execute()
    job.collect()
    job.preserve()
    hook = repo / ".git/hooks/pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    with pytest.raises(RuntimeError, match="preservation failed"):
        job.register()

    hook.unlink()
    job.register()
    job.register()
    assert (repo / "experiments/INDEX.md").read_text().count(
        f"<!-- forest-run:{job.state['id']} -->"
    ) == 1


def test_scope_violation_is_preserved_but_not_registered(repo, tmp_path, fake_codex):
    job = create(repo, tmp_path)
    job.prepare()
    job.execute()
    (job.worktree / "docs/operations.md").write_text("Unauthorized change")
    with pytest.raises(RuntimeError, match="out-of-scope"):
        job.collect()

    assert job.state["phase"] == "collecting"
    assert list((job.run / "recovery").glob("*/files.tar"))
    assert job.state["id"] not in (repo / "experiments/INDEX.md").read_text()


def test_preserves_development_instructions_and_their_deployment_hash(
    repo, tmp_path, fake_codex
):
    # Given a completed exploration with updated development instructions and generated hashes.
    job = create(repo, tmp_path)
    job.prepare()
    job.execute()
    name = ".apm/instructions/naedo.instructions.md"
    instruction = job.worktree / name
    instruction.parent.mkdir(parents=True)
    instruction.write_text("---\napplyTo: naedo/**\n---\n\nExample rule.\n")
    lock = job.worktree / "apm.lock.yaml"
    lock.write_text(lock.read_text().replace("0" * 64, "1" * 64))

    # When post-exit collection, preservation, and registration run.
    result = job.advance(recovery=True)

    # Then instructions and hashes enter the saved revision without directly changing the shared revision.
    assert result["phase"] == "registered"
    assert "Example rule." in repository.git(
        repo, "show", result["saved_commit"] + ":" + name
    )
    assert "1" * 64 in repository.git(
        repo, "show", result["saved_commit"] + ":apm.lock.yaml"
    )
    assert not (repo / name).exists()


@pytest.mark.parametrize(
    "path",
    [".apm/instructions/naedo-forest.instructions.md", "apm.yml", "apm.lock.yaml"],
)
def test_instruction_updates_do_not_allow_governance_changes(
    repo, tmp_path, fake_codex, path
):
    # Given changed shared instructions, APM settings, or lockfile dependencies during exploration.
    job = create(repo, tmp_path)
    job.prepare()
    job.execute()
    target = job.worktree / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("dependencies: [unauthorized]\n")

    # When collecting, then reject the out-of-scope changes without registration.
    with pytest.raises(RuntimeError, match="out-of-scope"):
        job.collect()

    assert job.state["id"] not in (repo / "experiments/INDEX.md").read_text()


def test_crash_after_commit_before_state_update_reuses_saved_commit(
    repo, tmp_path, fake_codex, monkeypatch
):
    # Given a successful preservation commit followed by a failed checkpoint write.
    job = create(repo, tmp_path)
    job.prepare()
    job.execute()
    job.collect()
    original = job.save

    def fail_after_commit():
        if job.state["phase"] == "saved":
            raise OSError("simulated state checkpoint failure")

        original()

    monkeypatch.setattr(job, "save", fail_after_commit)
    with pytest.raises(OSError):
        job.preserve()

    saved = repository.git(job.worktree, "rev-parse", "HEAD")
    # When resuming from the preservation checkpoint, create no extra commit.
    resumed = experiment.Runner(job.run)
    resumed.advance(recovery=True)
    assert resumed.state["saved_commit"] == saved


def test_crash_after_registration_commit_recovers_without_duplicate(
    repo, tmp_path, fake_codex, monkeypatch
):
    job = create(repo, tmp_path)
    job.prepare()
    job.execute()
    job.collect()
    job.preserve()
    original = job.save

    def fail_after_registration():
        if job.state["phase"] == "registered":
            raise OSError("simulated registration checkpoint failure")

        original()

    monkeypatch.setattr(job, "save", fail_after_registration)
    with pytest.raises(OSError):
        job.register()

    commit = repository.git(repo, "rev-parse", "HEAD")
    resumed = experiment.Runner(job.run)
    resumed.advance(recovery=True)
    assert resumed.state["phase"] == "registered"
    assert repository.git(repo, "rev-parse", "HEAD") == commit


def test_invalid_result_cannot_be_registered_as_observed(repo, tmp_path, fake_codex):
    job = create(repo, tmp_path)
    job.prepare()
    job.execute()
    result = json.loads((job.run / "result.json").read_text())
    result["record"] = "../outside.md"
    (job.run / "result.json").write_text(json.dumps(result))
    job.advance()
    assert job.state["reason"] == "invalid_result"
    assert job.state["result"]["status"] == "unresolved"


def test_moved_saved_branch_prevents_registration(repo, tmp_path, fake_codex):
    # Given a preserved result whose branch was moved by another actor.
    job = create(repo, tmp_path)
    job.prepare()
    job.execute()
    job.collect()
    job.preserve()
    repository.git(job.worktree, "commit", "--allow-empty", "-m", "test: change branch")
    # When registration is attempted, do not accept the changed reference as valid.
    with pytest.raises(RuntimeError, match="branch moved"):
        job.register()

    assert job.state["id"] not in (repo / "experiments/INDEX.md").read_text()
