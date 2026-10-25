from __future__ import annotations

import pytest

import mutagi.execution.round as round_controller
import mutagi.execution.series as series
from mutagi.execution.policy import validate_proposal
from tests.support.plans import LIMITS, PROPOSAL


def test_integration_changes_next_count_prompt_and_time_within_authority(adaptive_case):
    # Given a first round with two tasks and an integrator proposing only one next task.
    repo, directory, calls = adaptive_case
    initial = series.Series(directory).state
    # When both rounds of the bounded series run.
    final = series.Series(directory).advance()
    second = round_controller.Round(
        repo / ".mutagi/runs/rounds" / final["rounds"][1]["id"]
    ).state
    # Then the accepted proposal is saved and used next without increasing overall limits.
    assert final["phase"] == "completed"
    assert len(calls.read_text().splitlines()) == 5
    assert len(second["plan"]["tasks"]) == 1
    assert "Change the premise" in second["plan"]["tasks"][0]["prompt"]
    assert second["plan"]["seconds"] == 180
    assert final["rounds"][1]["allocation"]["accepted_proposal"] == PROPOSAL
    assert final["deadline"] == initial["deadline"]
    assert second["plan"]["base"] == final["rounds"][0]["published_commit"]


@pytest.mark.parametrize(
    "change",
    [
        {"tasks": []},
        {"tasks": PROPOSAL["tasks"] * 4},
        {"round_seconds": 601},
        {"experiment_seconds": 19},
        {"integration_seconds": float("nan")},
        {"tasks": [{"prompt": "Invalid external parent", "parent": 0}]},
        {"model": "other"},
    ],
)
def test_proposal_cannot_expand_authority(change):
    # Given a structurally valid proposal containing one unauthorized change.
    proposal = {**PROPOSAL, **change}
    # When validated, then no silently clamped or substituted plan is accepted.
    with pytest.raises(ValueError):
        validate_proposal(proposal, LIMITS)


def test_changed_adaptation_assignment_blocks_recovery(adaptive_case):
    # Given a saved round whose permissions changed after assignment.
    _, directory, _ = adaptive_case
    controller = series.Series(directory)
    current = controller.allocate(0)
    current.state["adaptation"]["max_tasks"] = 100
    current.save()
    # When the series resumes, then reject the changed permissions.
    with pytest.raises(RuntimeError, match="assignment"):
        series.Series(directory).advance(recovery=True)
