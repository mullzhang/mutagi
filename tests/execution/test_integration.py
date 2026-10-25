from __future__ import annotations

import json

import pytest

import mutagi.execution.experiment as experiment
import mutagi.execution.integration as integration_controller
import mutagi.execution.round as round_controller
import mutagi.execution.series as series
import mutagi.project.repository as repository
from tests.support.execution import completed_jobs


def test_integration_scope_violation_blocks_publication(round_case, monkeypatch):
    # Given an integration session changing out-of-scope operational instructions.
    repo, directory, _ = round_case
    original = integration_controller.Integration.execute

    def violate_scope(self):
        original(self)
        (self.worktree / "docs/operations.md").write_text("Out of scope\n")

    monkeypatch.setattr(integration_controller.Integration, "execute", violate_scope)
    # When integration results are collected.
    with pytest.raises(RuntimeError, match="out-of-scope"):
        round_controller.Round(directory).advance()

    # Then preserve evidence for review without publishing to the shared tree.
    state = round_controller.Round(directory).state
    assert state["next"] == 2
    assert not (repo / "knowledge/notes/integrated.md").exists()
    assert list(
        (repo / ".mutagi/runs" / state["jobs"][2]["id"] / "recovery").glob(
            "*/files.tar"
        )
    )


def test_interrupted_integration_is_saved_without_publication(round_case, monkeypatch):
    # Given changes interrupted by the integration deadline.
    repo, directory, _ = round_case
    original = integration_controller.Integration.execute

    def timeout(self):
        original(self)
        self.state["reason"] = "time_limit"
        self.save()

    monkeypatch.setattr(integration_controller.Integration, "execute", timeout)
    # When the interrupted integration is preserved.
    result = round_controller.Round(directory).advance()
    # Then retain the branch without publishing unverified changes.
    integration = completed_jobs(repo, result)[2]
    assert not integration["published"]
    assert integration["reason"] == "time_limit"
    assert not (repo / "knowledge/notes/integrated.md").exists()
    assert repository.git(
        repo, "show", integration["saved_commit"] + ":knowledge/notes/integrated.md"
    )


def test_publication_checkpoint_loss_does_not_duplicate_commit(round_case, monkeypatch):
    # Given a successful publication commit followed by a lost final checkpoint.
    repo, directory, calls = round_case
    original = integration_controller.Integration.save

    def fail_published(self):
        if self.state["phase"] == "registered":
            raise OSError("publication checkpoint lost")

        original(self)

    with monkeypatch.context() as m:
        m.setattr(integration_controller.Integration, "save", fail_published)
        with pytest.raises(OSError, match="publication checkpoint"):
            round_controller.Round(directory).advance()

    published = repository.git(repo, "rev-parse", "HEAD")
    # When a new controller compares the saved tree and publication marker.
    result = round_controller.Round(directory).advance(recovery=True)
    # Then neither the integration agent nor the publication commit is duplicated.
    assert result["phase"] == "completed"
    assert repository.git(repo, "rev-parse", "HEAD") == published
    assert len(calls.read_text().splitlines()) == 3


def test_failed_publication_commit_resumes_exact_staged_tree(round_case):
    # Given a shared-branch hook rejecting final knowledge publication.
    repo, directory, calls = round_case
    hook = repo / ".git/hooks/pre-commit"
    hook.write_text(
        "#!/bin/sh\nif [ \"$(git branch --show-current)\" = main ] && git diff --cached --name-only | grep -q '^knowledge/notes/integrated.md$'; then exit 1; fi\n"
    )
    hook.chmod(0o755)
    with pytest.raises(RuntimeError, match="preservation failed"):
        round_controller.Round(directory).advance()

    # When the hook is removed and the round resumes.
    hook.unlink()
    result = round_controller.Round(directory).advance(recovery=True)
    # Then publish exactly the saved tree without rerunning the agent.
    assert result["phase"] == "completed"
    assert not repository.git(repo, "status", "--porcelain")
    assert len(calls.read_text().splitlines()) == 3


def test_integration_cannot_remove_source_registration(round_case, monkeypatch):
    # Given a completed integration that accidentally removed a source registration.
    repo, directory, _ = round_case
    original = integration_controller.Integration.execute

    def remove_catalog(self):
        original(self)
        (self.worktree / "experiments/INDEX.md").write_text("# Missing sources\n")

    monkeypatch.setattr(integration_controller.Integration, "execute", remove_catalog)
    # When publication validates the saved integration tree.
    with pytest.raises(
        RuntimeError, match="a saved experiment is missing from the shared catalog"
    ):
        round_controller.Round(directory).advance()

    # Then retain both source registrations in the shared catalog and preserve the integration.
    assert (repo / "experiments/INDEX.md").read_text().count("<!-- forest-run:") == 2
    state = round_controller.Round(directory).state
    integration = json.loads(
        (repo / ".mutagi/runs" / state["jobs"][2]["id"] / "state.json").read_text()
    )
    assert integration["phase"] == "saved"


def test_invalid_integration_proposal_is_saved_without_publication(
    adaptive_case, fake_codex
):
    # Given an integrator proposing a time budget above the authorized limit.
    repo, directory, calls = adaptive_case
    with fake_codex.open("a") as stream:
        stream.write(
            "\nif s.get('adaptation'):\n value=json.loads(out.read_text());value['next_round']['round_seconds']=999999;out.write_text(json.dumps(value))\n"
        )

    # When results are collected, then preserve evidence without starting later rounds.
    result = series.Series(directory).advance()
    assert result["phase"] == "integration_deferred"
    assert len(calls.read_text().splitlines()) == 3
    first = round_controller.Round(
        repo / ".mutagi/runs/rounds" / result["rounds"][0]["id"]
    ).state
    saved = experiment.Runner(repo / ".mutagi/runs" / first["jobs"][-1]["id"]).state
    assert saved["reason"] == "invalid_result"
    assert saved["phase"] == "registered"
    assert saved["published"] is False
    assert repository.git(repo, "rev-parse", saved["branch"]) == saved["saved_commit"]
