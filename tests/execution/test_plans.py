from __future__ import annotations

import copy

import pytest

import mutagi.execution.round as round_controller
import mutagi.execution.series as series
from tests.support.execution import create


@pytest.mark.parametrize(
    "defect", ["self_parent", "nan_budget", "zero_reserve", "extra_field"]
)
def test_invalid_round_plan_is_rejected_before_allocation(round_case, defect):
    # Given a plan with invalid dependencies, budgets, or fields.
    repo, directory, calls = round_case
    plan = round_controller.Round(directory).state["plan"]
    if defect == "self_parent":
        plan["tasks"][0]["parent"] = 0
    elif defect == "nan_budget":
        plan["seconds"] = float("nan")
    elif defect == "zero_reserve":
        plan["reserve_seconds"] = 0
    else:
        plan["automatic_retries"] = 3

    # When validation runs before round creation.
    with pytest.raises(ValueError):
        round_controller.create_round(repo, plan)

    # Then no session has started.
    assert not calls.exists()


@pytest.mark.parametrize(
    "seconds,reserve", [(0, 0), (float("inf"), 5), (5, 5), (5, -1)]
)
def test_invalid_budget_rejected(repo, tmp_path, seconds, reserve):
    with pytest.raises(ValueError, match="budget"):
        create(repo, tmp_path, seconds=seconds, reserve=reserve)


@pytest.mark.parametrize(
    "defect", ["nan", "single_round", "unknown_field", "bad_parent"]
)
def test_invalid_series_plan_is_rejected_before_allocation(round_environment, defect):
    # Given an invalid series plan with no allocated state.
    repo, plan, calls = round_environment
    tasks = plan.pop("tasks")
    plan.update(
        round_seconds=600, rounds=[{"tasks": tasks}, {"tasks": copy.deepcopy(tasks)}]
    )
    if defect == "nan":
        plan["seconds"] = float("nan")
    elif defect == "single_round":
        plan["rounds"].pop()
    elif defect == "unknown_field":
        plan["retry"] = True
    else:
        plan["rounds"][1]["tasks"][0]["parent"] = 0

    # When the plan is submitted.
    with pytest.raises(ValueError):
        series.create_series(repo, plan)

    # Then neither a series nor a model session exists.
    assert not calls.exists()
    assert not list((repo / ".mutagi/runs/series").glob("*/state.json"))
