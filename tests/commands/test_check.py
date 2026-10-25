from __future__ import annotations

import json

import pytest

import mutagi.commands.check as check
import mutagi.execution.plans as plans
import mutagi.execution.round as round_controller
import mutagi.execution.series as series
from mutagi.project.repository import git
from tests.support.plans import plan_for
from tests.support.repositories import commit, snapshot


def test_check_is_read_only_and_does_not_run_preparation(repo, diagnostic_codex):
    before = snapshot(repo)
    results = check.inspect(repo, "HEAD")
    assert all(passed for _, passed, _ in results), results
    assert snapshot(repo) == before
    assert not (repo / ".mutagi/runs").exists()
    preparation = next(detail for name, _, detail in results if name == "Preparation")
    assert "not executed" in preparation
    assert "private-auth-output" not in repr(results)


def test_check_reports_independent_failures_and_committed_configuration(
    repo, diagnostic_codex, monkeypatch
):
    path = repo / ".mutagi/config.toml"
    valid = path.read_text()
    path.write_text('name = "incomplete"\n')
    commit(repo)
    path.write_text(valid)
    monkeypatch.setenv("TEST_AUTH_STATUS", "1")
    results = {
        name: (passed, detail) for name, passed, detail in check.inspect(repo, "HEAD")
    }
    assert results["Working project files"][0]
    assert not results["Committed project files"][0]
    assert "missing required field" in results["Committed project files"][1]
    assert not results["Working tree"][0]
    assert not results["Authentication"][0]
    assert "codex login" in results["Authentication"][1]
    assert "private-auth-output" not in repr(results)
    assert not (repo / ".mutagi/runs").exists()


def test_check_detects_tracked_private_state(repo, diagnostic_codex):
    state = repo / ".mutagi/runs/accidental.txt"
    state.parent.mkdir()
    state.write_text("private state")
    git(repo, "add", "-f", ".mutagi/runs/accidental.txt")
    results = {
        name: (passed, detail) for name, passed, detail in check.inspect(repo, "HEAD")
    }
    assert not results["Private storage"][0]
    assert "Git index" in results["Private storage"][1]


def test_check_resumes_the_owner_of_an_unfinished_child(repo):
    plan = plan_for("series")
    plan["model"] = "chosen-model"
    directory = series.create_series(repo, plan)
    state = json.loads((directory / "state.json").read_text())
    round_id = state["rounds"][0]["id"]
    round_directory = round_controller.create_round(
        repo,
        plans.round_plan(plan, plan["rounds"][0]["tasks"]),
        identifier=round_id,
        series_id=state["id"],
        prior_sources=[],
    )
    round_state = json.loads((round_directory / "state.json").read_text())
    child = repo / ".mutagi/runs" / round_state["jobs"][0]["id"]
    child.mkdir()
    (child / "state.json").write_text(
        json.dumps({"id": child.name, "phase": "preparing", "series_id": state["id"]})
    )
    with pytest.raises(ValueError) as caught:
        check.unfinished_work(repo)
    assert str(caught.value).count("mutagi series") == 1
    assert "mutagi experiment" not in str(caught.value)
    assert "mutagi round" not in str(caught.value)
