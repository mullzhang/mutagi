from __future__ import annotations

import copy
import json
import sys
import time

import pytest

import mutagi.commands.experiment as experiment_command
import mutagi.commands.round as round_command
import mutagi.execution.experiment as experiment
import mutagi.execution.integration as integration_controller
import mutagi.execution.plans as plans
import mutagi.execution.round as round_controller
import mutagi.execution.series as series
import mutagi.project.repository as repository
import mutagi.runtime.storage as storage


def allocated_rounds(directory):
    controller = series.Series(directory)
    return [
        round_controller.Round(controller.round_directory(item)).state
        for item in controller.state["rounds"]
        if item["allocation"]
    ]


@pytest.fixture
def series_case(round_environment):
    repo, plan, calls = round_environment
    tasks = plan.pop("tasks")
    plan.update(
        seconds=900,
        round_seconds=600,
        rounds=[{"tasks": tasks}, {"tasks": copy.deepcopy(tasks)}],
    )
    return repo, series.create_series(repo, plan), calls


def test_two_rounds_use_published_knowledge_and_feedback_once(series_case):
    # Given two rounds, each with independent exploration, inheritance, and integration.
    repo, directory, calls = series_case
    initial = series.Series(directory).state
    # When the series completes and a new controller resumes it.
    result = series.Series(directory).advance()
    head = repository.git(repo, "rev-parse", "HEAD")
    repeated = series.Series(directory).advance(recovery=True)
    first, second = allocated_rounds(directory)
    # Then the next round starts from the published tree and receives fixed evidence.
    assert result["phase"] == repeated["phase"] == "completed"
    assert second["plan"]["base"] == first["jobs"][-1]["registry_commit"]
    assert first["id"] in second["plan"]["tasks"][0]["prompt"]
    assert first["jobs"][0]["saved_commit"] in second["plan"]["tasks"][0]["prompt"]
    assert (
        len(calls.read_text().splitlines())
        == len(set(calls.read_text().splitlines()))
        == 6
    )
    assert repository.git(repo, "rev-parse", "HEAD") == head
    assert result["deadline"] == initial["deadline"]
    assert all(r["deadline"] <= result["deadline"] for r in (first, second))
    assert not repository.git(repo, "status", "--porcelain")


@pytest.mark.parametrize("state_written", [False, True])
def test_round_allocation_recovery_keeps_id_start_and_budget(
    series_case, monkeypatch, state_written
):
    # Given allocation interrupted before or after writing the managed round's state.
    repo, directory, calls = series_case
    original_create = round_controller.create_round
    original_json = storage.atomic_json

    def allocate_then_fail(*args, **kwargs):
        original_create(*args, **kwargs)
        raise OSError("allocation interrupted")

    def fail_round_state(path, value):
        if path.name == "state.json" and path.parent.parent.name == "rounds":
            path.with_suffix(".tmp").write_text('{"partial":')
            raise OSError("allocation interrupted")

        original_json(path, value)

    with monkeypatch.context() as m:
        if state_written:
            m.setattr(series, "create_round", allocate_then_fail)
        else:
            m.setattr(round_controller, "atomic_json", fail_round_state)

        with pytest.raises(OSError, match="allocation"):
            series.Series(directory).advance()

    intent = series.Series(directory).state["rounds"][0]
    # When the series resumes from disk.
    result = series.Series(directory).advance(recovery=True)
    first = allocated_rounds(directory)[0]
    # Then allocation identity and deadlines remain unchanged, with no extra CLI sessions.
    assert result["phase"] == "completed"
    assert first["id"] == intent["id"]
    assert first["started_at"] == storage.utc(intent["allocation"]["started"])
    assert (
        first["deadline"]
        == intent["allocation"]["started"] + intent["allocation"]["plan"]["seconds"]
    )
    assert len(calls.read_text().splitlines()) == 6
    assert len(list((repo / ".mutagi/runs/rounds").glob("*/state.json"))) == 2


def test_lost_series_checkpoint_after_publication_does_not_repeat_round(
    series_case, monkeypatch
):
    # Given a published round whose parent series checkpoint could not be saved.
    repo, directory, calls = series_case
    original = series.Series.save

    def fail_after_round(self):
        if self.state["next"] == 1:
            raise OSError("series checkpoint lost")

        original(self)

    with monkeypatch.context() as m:
        m.setattr(series.Series, "save", fail_after_round)
        with pytest.raises(OSError, match="checkpoint"):
            series.Series(directory).advance()

    assert series.Series(directory).state["next"] == 0
    published = repository.git(repo, "rev-parse", "HEAD")
    # When the original series reconciles the completed round.
    result = series.Series(directory).advance(recovery=True)
    # Then only the next round starts, using the existing published revision.
    assert result["phase"] == "completed"
    assert allocated_rounds(directory)[1]["plan"]["base"] == published
    assert len(calls.read_text().splitlines()) == 6


def test_series_budget_expiry_between_rounds_preserves_unstarted_slots(
    series_case, monkeypatch
):
    # Given the overall deadline expires when the first round ends.
    _, directory, calls = series_case
    original = series.Series.save

    def expire_after_round(self):
        if self.state["next"] == 1:
            self.state["deadline"] = time.time() - 1

        original(self)

    monkeypatch.setattr(series.Series, "save", expire_after_round)
    # When the next allocation is checked and later resumed.
    result = series.Series(directory).advance()
    repeated = series.Series(directory).advance(recovery=True)
    # Then the second round does not start and resumption cannot extend the deadline.
    assert result["phase"] == repeated["phase"] == "budget_exhausted"
    assert result["not_started"] == [result["rounds"][1]["id"]]
    assert result["rounds"][1]["allocation"] is None
    assert len(calls.read_text().splitlines()) == 3


def test_deferred_integration_stops_future_rounds(series_case, monkeypatch):
    # Given the first integration is interrupted by its deadline.
    _, directory, calls = series_case
    original = integration_controller.Integration.execute

    def timeout(self):
        original(self)
        self.state["reason"] = "time_limit"
        self.save()

    monkeypatch.setattr(integration_controller.Integration, "execute", timeout)
    # When the series receives a saved but unpublished integration.
    result = series.Series(directory).advance()
    repeated = series.Series(directory).advance(recovery=True)
    # Then the branch is preserved without starting the next agent or an automatic retry.
    assert result["phase"] == repeated["phase"] == "integration_deferred"
    assert result["rounds"][1]["allocation"] is None
    assert len(calls.read_text().splitlines()) == 3


def test_later_integration_cannot_remove_earlier_round_registration(
    series_case, monkeypatch
):
    # Given the second integration accidentally removes the first round's catalog marker.
    repo, directory, _ = series_case
    original = integration_controller.Integration.execute

    def drop_previous_marker(self):
        original(self)
        task = json.loads((self.run / "task.txt").read_text())
        if task["prior_sources"]:
            marker = f"<!-- forest-run:{task['prior_sources'][0]['id']} -->"
            path = self.worktree / "experiments/INDEX.md"
            path.write_text(path.read_text().replace(marker, ""))

    monkeypatch.setattr(
        integration_controller.Integration, "execute", drop_previous_marker
    )
    # When current and prior source registrations are checked before publication.
    with pytest.raises(
        RuntimeError, match="a saved experiment is missing from the shared catalog"
    ):
        series.Series(directory).advance()

    # Then the invalid integration is preserved without deleting shared evidence.
    first, second = allocated_rounds(directory)
    assert (
        f"<!-- forest-run:{first['jobs'][0]['id']} -->"
        in (repo / "experiments/INDEX.md").read_text()
    )
    integration = json.loads(
        (repo / ".mutagi/runs" / second["jobs"][-1]["id"] / "state.json").read_text()
    )
    assert integration["phase"] == "saved"
    assert series.Series(directory).state["next"] == 1


def test_operator_stop_requires_explicit_series_resume(series_case, monkeypatch):
    # Given the operator interrupts the first CLI invocation.
    _, directory, calls = series_case
    with monkeypatch.context() as m:
        m.setattr(
            experiment.Runner,
            "execute",
            lambda _: (_ for _ in ()).throw(KeyboardInterrupt()),
        )
        stopped = series.Series(directory).advance()

    assert stopped["phase"] == "stopped"
    assert series.Series(directory).advance()["phase"] == "stopped"
    assert not calls.exists()
    # When the operator explicitly resumes the same series.
    result = series.Series(directory).advance(recovery=True)
    # Then only unfinished slots run, retaining the original deadline.
    assert result["phase"] == "completed"
    assert result["deadline"] == stopped["deadline"]
    assert len(calls.read_text().splitlines()) == 5


@pytest.mark.parametrize("defect", ["shared_history", "saved_branch", "catalog"])
def test_changed_evidence_blocks_next_round(series_case, monkeypatch, defect):
    # Given the first round completed and execution stopped before the next allocation.
    repo, directory, calls = series_case
    original = series.Series.save

    def pause_after_round(self):
        original(self)
        if self.state["next"] == 1:
            raise OSError("pause at boundary")

    with monkeypatch.context() as m:
        m.setattr(series.Series, "save", pause_after_round)
        with pytest.raises(OSError, match="boundary"):
            series.Series(directory).advance()

    first = allocated_rounds(directory)[0]
    if defect == "saved_branch":
        job = json.loads(
            (repo / ".mutagi/runs" / first["jobs"][0]["id"] / "state.json").read_text()
        )
        repository.git(repo, "update-ref", "refs/heads/" + job["branch"], job["base"])
    else:
        path = repo / (
            "experiments/INDEX.md" if defect == "catalog" else "knowledge/notes/seed.md"
        )
        path.write_text("Changed after publication\n")
        repository.git(repo, "add", str(path))
        repository.git(repo, "commit", "-m", "test: change shared evidence")

    # When a new controller attempts to continue.
    with pytest.raises(RuntimeError):
        series.Series(directory).advance(recovery=True)

    # Then only the three existing sessions have been invoked.
    assert len(calls.read_text().splitlines()) == 3


def test_pending_series_blocks_other_entrypoints(series_case, tmp_path):
    # Given an allocated series before its first round exists.
    repo, directory, calls = series_case
    plan = series.Series(directory).state["plan"]
    task = tmp_path / "task.txt"
    task.write_text("Another task")
    # When another series, a standalone round, and a standalone experiment are requested.
    with pytest.raises(RuntimeError, match="unfinished series"):
        series.create_series(repo, plan)

    with pytest.raises(RuntimeError, match="unfinished series"):
        round_controller.create_round(
            repo, plans.round_plan(plan, plan["rounds"][0]["tasks"])
        )

    with pytest.raises(RuntimeError, match="unfinished series"):
        experiment.create(repo, "HEAD", task.read_text(), "test", "medium", 60, 10)

    # Then no session starts and only the original series retains ownership.
    assert not calls.exists()
    assert len(list((repo / ".mutagi/runs/series").glob("*/state.json"))) == 1


def test_modified_round_budget_blocks_series_resume(series_case):
    # Given a round whose deadline changed after allocation.
    _, directory, calls = series_case
    controller = series.Series(directory).allocate(0)
    controller.state["deadline"] += 100
    controller.save()
    # When the series resumes with its recorded assignment.
    with pytest.raises(RuntimeError, match="series assignment"):
        series.Series(directory).advance(recovery=True)

    # Then the increased budget is rejected before model usage.
    assert not calls.exists()


def test_remaining_series_time_clamps_round_and_child(series_case):
    # Given less overall time remains than the configured round and experiment limits.
    _, directory, calls = series_case
    controller = series.Series(directory)
    controller.state["deadline"] = time.time() + 30
    controller.save()
    # When the next round and its first child are allocated.
    next_round = controller.allocate(0)
    child = next_round.allocate(0)
    # Then neither allocation can extend the fixed overall deadline.
    assert next_round.state["deadline"] <= controller.state["deadline"]
    assert child.state["deadline"] <= next_round.state["deadline"]
    assert child.state["exploration_deadline"] < child.state["deadline"]
    assert not calls.exists()


def test_round_budget_exhaustion_stops_the_whole_series(series_case, monkeypatch):
    # Given the round budget expires after preserving its first child.
    _, directory, calls = series_case
    original = round_controller.Round.save

    def expire_current_round(self):
        if self.state["next"] == 1:
            self.state["deadline"] = time.time() - 1

        original(self)

    monkeypatch.setattr(round_controller.Round, "save", expire_current_round)
    # When the series receives the unfinished round.
    result = series.Series(directory).advance()
    # Then remaining overall time does not authorize skipping unfinished integration.
    assert result["phase"] == "budget_exhausted"
    assert result["incomplete_round"] == result["rounds"][0]["id"]
    assert result["rounds"][1]["allocation"] is None
    assert len(allocated_rounds(directory)[0]["not_started"]) == 2
    assert len(calls.read_text().splitlines()) == 1


def test_preservation_failure_blocks_all_later_rounds_until_resume(
    series_case, monkeypatch
):
    # Given a successful model invocation followed by failed preservation of the first child.
    _, directory, calls = series_case
    with monkeypatch.context() as m:
        m.setattr(
            experiment.Runner,
            "preserve",
            lambda _: (_ for _ in ()).throw(RuntimeError("save failed")),
        )
        with pytest.raises(RuntimeError, match="save failed"):
            series.Series(directory).advance()

    assert len(calls.read_text().splitlines()) == 1
    assert series.Series(directory).state["rounds"][1]["allocation"] is None
    # When the same series resumes after preservation becomes possible.
    result = series.Series(directory).advance(recovery=True)
    # Then the remaining rounds complete without rerunning the first model.
    assert result["phase"] == "completed"
    assert len(calls.read_text().splitlines()) == 6


@pytest.mark.parametrize("entrypoint", ["round", "experiment"])
def test_owned_child_cannot_be_resumed_through_standalone_cli(
    series_case, monkeypatch, entrypoint
):
    # Given a child owned by a series controller.
    repo, directory, calls = series_case
    controller = series.Series(directory).allocate(0)
    module = round_command
    identifier = controller.state["id"]
    if entrypoint == "experiment":
        module = experiment_command
        identifier = controller.allocate(0).state["id"]

    monkeypatch.setattr(
        sys, "argv", ["runner", "--repo", str(repo), "resume", identifier]
    )
    # When a standalone CLI is asked to resume that child.
    with pytest.raises(RuntimeError, match="mutagi series resume"):
        module.main()

    # Then no model starts outside the owning series.
    assert not calls.exists()
