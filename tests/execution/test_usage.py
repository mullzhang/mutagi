from __future__ import annotations

import json

import pytest

import mutagi.execution.experiment as experiment
import mutagi.execution.plans as plans
import mutagi.execution.round as round_controller
import mutagi.execution.series as series
import mutagi.runtime.storage as storage
from mutagi.execution.usage import measured, remaining, validate_limits
from tests.support.plans import RATES


def test_usage_counts_cached_input_once_and_reasoning_inside_output():
    # Given usage totals with cache hits and reasoning included in output.
    total = {
        "inputTokens": 1000,
        "cachedInputTokens": 800,
        "outputTokens": 100,
        "reasoningOutputTokens": 80,
        "totalTokens": 1100,
    }
    # When rates are applied, then only uncached input uses the regular input rate.
    result = measured(total, RATES)
    assert result["tokens"] == 1100
    assert result["cost"] == "0.195"


@pytest.mark.parametrize(
    "change",
    [
        {"cachedInputTokens": 1001},
        {"totalTokens": 1101},
        {"outputTokens": -1},
        {"cacheWriteInputTokens": 1},
    ],
)
def test_unknown_or_inconsistent_billing_is_rejected(change):
    # Given usage that cannot be priced by the explicitly selected rate card.
    total = {
        "inputTokens": 1000,
        "cachedInputTokens": 800,
        "outputTokens": 100,
        "reasoningOutputTokens": 80,
        "totalTokens": 1100,
        **change,
    }
    # When measured, then stop with unknown cost instead of inventing zero cost.
    with pytest.raises(ValueError):
        measured(total, RATES)


def test_interrupted_usage_is_not_treated_as_complete(adaptive_case):
    # Given a completed fixture series with interrupted usage for one session.
    repo, directory, _ = adaptive_case
    final = series.Series(directory).advance()
    final["plan"]["usage_limits"] = {"tokens": 10000, "cost": 10, "rates": RATES}
    for path in (repo / ".mutagi/runs").glob("*/state.json"):
        state = json.loads(path.read_text())
        if state.get("series_id") == final["id"]:
            storage.atomic_json(
                path.parent / "usage.json",
                {"tokens": 100, "cost": "0.1", "complete": True},
            )

    first = (
        repo
        / ".mutagi/runs"
        / round_controller.Round(
            repo / ".mutagi/runs/rounds" / final["rounds"][0]["id"]
        ).state["jobs"][0]["id"]
    )
    storage.atomic_json(
        first / "usage.json", {"tokens": 150, "cost": "0.2", "complete": False}
    )
    # When the ledger is checked before allocation, then count known usage and stop on uncertainty.
    value = remaining(repo, final)
    assert value["used_tokens"] == 550
    assert value["estimated_cost"] == "0.6"
    assert value["unknown_sessions"] == [first.name]


def test_rate_card_model_must_match_plan(adaptive_case):
    # Given an otherwise valid plan with a rate card for a different model.
    _, directory, _ = adaptive_case
    plan = series.Series(directory).state["plan"]
    plan["usage_limits"] = {
        "tokens": 10000,
        "cost": 10,
        "rates": {**RATES, "model": "other"},
    }
    # When starting, then reject the mismatch before invoking a model.
    with pytest.raises(ValueError, match="model"):
        plans.validate_series(plan)


def test_invalid_usage_configuration_has_no_implicit_rates():
    # Given missing rates, then validation rejects them instead of assuming free usage.
    with pytest.raises(ValueError):
        validate_limits({"tokens": 100, "cost": 1, "rates": {}})


@pytest.mark.parametrize("usage_state", ["exhausted", "incomplete", "missing"])
def test_usage_stops_series_before_next_allocation(
    adaptive_case, monkeypatch, usage_state
):
    # Given a finite plan whose first session exhausts usage or leaves it unknown.
    repo, directory, calls = adaptive_case
    controller = series.Series(directory)
    controller.state["plan"]["usage_limits"] = {
        "tokens": 1000,
        "cost": 10,
        "rates": RATES,
    }
    controller.save()
    original = experiment.Runner.execute

    def measured_fixture(self):
        assigned = self.state.pop("usage_budget")
        assert assigned["tokens"] == 1000
        original(self)  # Run the existing fake CLI and real preservation logic.
        self.state["usage_budget"] = assigned
        self.save()
        if usage_state != "missing":
            storage.atomic_json(
                self.run / "usage.json",
                {
                    "tokens": 1000 if usage_state == "exhausted" else 100,
                    "cost": "0.1",
                    "complete": usage_state == "exhausted",
                },
            )

    monkeypatch.setattr(experiment.Runner, "execute", measured_fixture)
    # When the first result is preserved, then neither inheritance nor integration can consume more usage.
    result = controller.advance()
    expected = "usage_exhausted" if usage_state == "exhausted" else "usage_unknown"
    assert result["phase"] == expected
    current = round_controller.Round(
        repo / ".mutagi/runs/rounds" / result["rounds"][0]["id"]
    ).state
    assert current["next"] == 1
    assert current["jobs"][1]["allocation"] is None
    assert len(calls.read_text().splitlines()) == 1
    assert series.Series(directory).advance(recovery=True)["phase"] == expected
