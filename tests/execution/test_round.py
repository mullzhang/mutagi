from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

import mutagi.execution.experiment as experiment
import mutagi.execution.round as round_controller
import mutagi.project.repository as repository
import mutagi.runtime.storage as storage
from tests.support.execution import completed_jobs


def test_round_runs_inheritance_and_separate_integration_once(round_case):
    # Given two planned explorations and a separately allocated integration session.
    repo, directory, calls = round_case
    initial = round_controller.Round(directory).state
    # When the round runs and is resumed after completion.
    result = round_controller.Round(directory).advance()
    head = repository.git(repo, "rev-parse", "HEAD")
    repeated = round_controller.Round(directory).advance(recovery=True)
    # Then all three sessions run once and inheritance starts from the saved parent.
    jobs = completed_jobs(repo, result)
    assert result["phase"] == repeated["phase"] == "completed"
    assert calls.read_text().splitlines() == [item["id"] for item in initial["jobs"]]
    assert jobs[1]["base"] == jobs[0]["saved_commit"]
    assert jobs[1]["parent_ids"] == [jobs[0]["id"]]
    assert jobs[2]["published"]
    assert repository.git(repo, "rev-parse", "HEAD") == head
    assert (repo / "knowledge/notes/integrated.md").exists()
    assert not list((repo / "experiments").glob("*_test-question_*"))
    assert all(job["deadline"] <= result["deadline"] for job in jobs)


def test_round_recovers_stale_parent_checkpoint_without_relaunch(
    round_case, monkeypatch
):
    # Given a lost parent checkpoint after child preservation and registration completed.
    repo, directory, calls = round_case
    original = round_controller.Round.save

    def fail_after_child(self):
        if self.state["next"] == 1:
            raise OSError("round checkpoint lost")

        original(self)

    with monkeypatch.context() as m:
        m.setattr(round_controller.Round, "save", fail_after_child)
        with pytest.raises(OSError, match="checkpoint"):
            round_controller.Round(directory).advance()

    assert round_controller.Round(directory).state["next"] == 0
    # When a new controller resumes from disk.
    result = round_controller.Round(directory).advance(recovery=True)
    # Then reconcile the completed child without duplicating sessions.
    assert result["phase"] == "completed"
    assert (
        len(calls.read_text().splitlines())
        == len(set(calls.read_text().splitlines()))
        == 3
    )
    assert len(completed_jobs(repo, result)) == 3


@pytest.mark.parametrize("allocation_written", [False, True])
def test_round_recovers_allocation_with_same_id_and_time(
    round_case, monkeypatch, allocation_written
):
    # Given a round-owned ID and child allocation interrupted around the state.json write.
    _, directory, calls = round_case
    original_create = experiment.create
    original_json = storage.atomic_json

    def allocate_then_fail(*args, **kwargs):
        original_create(*args, **kwargs)
        raise OSError("allocation interrupted")

    def fail_state(path, value):
        if path.name == "state.json" and value["phase"] == "preparing":
            path.with_suffix(".tmp").write_text('{"partial":')
            raise OSError("allocation interrupted")

        original_json(path, value)

    with monkeypatch.context() as m:
        if allocation_written:
            m.setattr(round_controller, "create", allocate_then_fail)
        else:
            m.setattr(experiment, "atomic_json", fail_state)

        with pytest.raises(OSError, match="allocation"):
            round_controller.Round(directory).advance()

    intent = round_controller.Round(directory).state["jobs"][0]
    # When the same round resumes after allocation failure.
    result = round_controller.Round(directory).advance(recovery=True)
    # Then retain the original ID and start time without creating extra children.
    state = json.loads(
        (
            Path(result["repo"]) / ".mutagi/runs" / intent["id"] / "state.json"
        ).read_text()
    )
    assert state["started_at"] == storage.utc(intent["allocation"]["started"])
    assert (
        state["deadline"]
        == intent["allocation"]["started"] + intent["allocation"]["seconds"]
    )
    assert calls.read_text().splitlines()[0] == intent["id"]
    assert len(calls.read_text().splitlines()) == 3


def test_pending_child_preservation_blocks_next_session(round_case, monkeypatch):
    # Given a first child unable to complete preservation.
    _, directory, calls = round_case
    with monkeypatch.context() as m:
        m.setattr(
            experiment.Runner,
            "preserve",
            lambda _: (_ for _ in ()).throw(RuntimeError("save failed")),
        )
        with pytest.raises(RuntimeError, match="save failed"):
            round_controller.Round(directory).advance()

    state = round_controller.Round(directory).state
    assert state["jobs"][1]["allocation"] is None
    assert len(calls.read_text().splitlines()) == 1
    # When preservation becomes possible and the round resumes.
    result = round_controller.Round(directory).advance(recovery=True)
    # Then advance only after preservation without restarting the first agent.
    assert result["phase"] == "completed"
    assert len(calls.read_text().splitlines()) == 3


def test_round_budget_exhaustion_does_not_start_next_session(round_case, monkeypatch):
    # Given the round budget expires after preserving its first child.
    _, directory, calls = round_case
    original = round_controller.Round.save

    def expire_after_child(self):
        if self.state["next"] == 1:
            self.state["deadline"] = time.time() - 1

        original(self)

    monkeypatch.setattr(round_controller.Round, "save", expire_after_child)
    # When the controller considers the next task and is later resumed.
    result = round_controller.Round(directory).advance()
    repeated = round_controller.Round(directory).advance(recovery=True)
    # Then record two unstarted slots without extending the budget or starting sessions.
    assert result["phase"] == repeated["phase"] == "budget_exhausted"
    assert len(result["not_started"]) == 2
    assert len(calls.read_text().splitlines()) == 1


def test_changed_assignment_blocks_resume(round_case):
    # Given an allocated run whose deadline no longer matches its round assignment.
    repo, directory, calls = round_case
    controller = round_controller.Round(directory)
    job = controller.allocate(0)
    job.state["deadline"] += 100
    job.save()
    # When a new controller resumes.
    with pytest.raises(RuntimeError, match="fixed assignment"):
        round_controller.Round(directory).advance(recovery=True)

    # Then retain the unexpected state for inspection without invoking a model.
    assert not calls.exists()
    assert (repo / ".mutagi/runs" / job.state["id"] / "state.json").exists()


def test_missing_registered_entry_blocks_next_session(round_case, monkeypatch):
    # Given a completed first child whose registration is missing from the shared catalog.
    repo, directory, calls = round_case
    original = round_controller.Round.save

    def stop_after_child(self):
        original(self)
        if self.state["next"] == 1:
            raise OSError("stop before next task")

    with monkeypatch.context() as m:
        m.setattr(round_controller.Round, "save", stop_after_child)
        with pytest.raises(OSError):
            round_controller.Round(directory).advance()

    (repo / "experiments/INDEX.md").write_text("# Lost registration\n")
    repository.git(repo, "add", "experiments/INDEX.md")
    repository.git(repo, "commit", "-m", "test: remove registration")
    # When resuming a checkpoint that marks the first child as complete.
    with pytest.raises(
        RuntimeError, match="a saved experiment is missing from the shared catalog"
    ):
        round_controller.Round(directory).advance(recovery=True)

    # Then the missing registration prevents a second model invocation.
    assert len(calls.read_text().splitlines()) == 1


def test_pending_round_rejects_second_controller(round_case):
    # Given an unfinished round with allocated session IDs.
    repo, directory, calls = round_case
    plan = round_controller.Round(directory).state["plan"]
    # When another round is requested in the same repository.
    with pytest.raises(RuntimeError, match="unfinished round"):
        round_controller.create_round(repo, plan)

    # Then neither a second round nor a session exists.
    assert len(list((repo / ".mutagi/runs/rounds").glob("*/state.json"))) == 1
    assert not calls.exists()


def test_operator_interruption_preserves_current_run_and_stops_sequence(
    round_case, monkeypatch
):
    # Given an operator interruption during the first model invocation.
    _, directory, calls = round_case
    with monkeypatch.context() as m:
        m.setattr(
            experiment.Runner,
            "execute",
            lambda _: (_ for _ in ()).throw(KeyboardInterrupt()),
        )
        result = round_controller.Round(directory).advance()

    # When the controller preserves the interrupted run and returns.
    assert result["phase"] == "stopped"
    assert result["next"] == 1
    assert not calls.exists()
    assert round_controller.Round(directory).advance()["phase"] == "stopped"
    # Then only explicit resumption starts remaining sessions within the same budget.
    resumed = round_controller.Round(directory).advance(recovery=True)
    assert resumed["phase"] == "completed"
    assert resumed["deadline"] == result["deadline"]
    assert len(calls.read_text().splitlines()) == 2
