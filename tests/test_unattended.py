from __future__ import annotations

import copy
import json

import pytest

from mutagi import run_experiment as runner
from mutagi import run_round as rounds
from mutagi import run_series as series
from mutagi import runner_process
from mutagi import series_service as service
from mutagi.series_policy import validate_proposal
from test_round import round_environment as round_environment
from mutagi.usage_budget import measured, remaining, validate_limits

pytest_plugins = ["test_runner"]

LIMITS = {
    "max_tasks": 3,
    "min_seconds": 20,
    "max_round_seconds": 600,
    "max_experiment_seconds": 120,
    "max_integration_seconds": 120,
}
PROPOSAL = {
    "reason": "Previous finite observation motivates a different premise",
    "tasks": [{"prompt": "Change the premise", "parent": None}],
    "round_seconds": 180,
    "experiment_seconds": 60,
    "integration_seconds": 60,
}
RATES = {
    "input": 250,
    "cached_input": 25,
    "output": 1250,
    "unit": "credits",
    "source": "https://learn.chatgpt.com/docs/pricing#token-rates",
    "model": "gpt-6-astra",
}


@pytest.fixture
def adaptive_case(round_environment, fake_codex):
    repo, plan, calls = round_environment
    tasks = plan.pop("tasks")
    plan.update(
        seconds=900,
        round_seconds=600,
        rounds=[{"tasks": tasks}, {"tasks": copy.deepcopy(tasks)}],
        adaptation=LIMITS,
    )
    with fake_codex.open("a") as stream:
        stream.write(
            "\nif s.get('adaptation'):\n value=json.loads(out.read_text());value['next_round']="
            + repr(PROPOSAL)
            + ";out.write_text(json.dumps(value))\n"
        )

    return repo, series.create_series(repo, plan), calls


def test_integration_changes_next_count_prompt_and_time_within_authority(adaptive_case):
    # Given a first round with two tasks and an integrator proposing only one next task.
    repo, directory, calls = adaptive_case
    initial = series.Series(directory).state
    # When both rounds of the bounded series run.
    final = series.Series(directory).advance()
    second = rounds.Round(repo / ".mutagi/runs/rounds" / final["rounds"][1]["id"]).state
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
            runner.atomic_json(
                path.parent / "usage.json",
                {"tokens": 100, "cost": "0.1", "complete": True},
            )

    first = (
        repo
        / ".mutagi/runs"
        / rounds.Round(repo / ".mutagi/runs/rounds" / final["rounds"][0]["id"]).state[
            "jobs"
        ][0]["id"]
    )
    runner.atomic_json(
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
        series.validate_plan(plan)


def test_prior_boot_processes_are_not_signalled(monkeypatch):
    # Given ownership from a previous OS boot with a reused PID.
    state = {
        "process": {
            "pid": 123,
            "identity": "old",
            "children": [],
            "boot_identity": "old-boot",
        }
    }
    monkeypatch.setattr(runner_process, "boot_identity", lambda: "new-boot")
    monkeypatch.setattr(
        runner_process,
        "stop",
        lambda *_: pytest.fail("Must not signal a previous boot PID"),
    )
    saved = []
    # When recovering, then clear stale ownership without touching current processes.
    runner_process.recover_process(state, lambda: saved.append(copy.deepcopy(state)))
    assert state["process"] is None
    assert saved == [{"process": None}]


def test_managed_service_recovers_checkpoint_and_keeps_operator_stop(
    adaptive_case, monkeypatch
):
    # Given a managed series supervisor interrupted before allocating the first round.
    repo, directory, calls = adaptive_case
    service.manage(repo, directory.name)
    original = series.Series.advance
    monkeypatch.setattr(
        series.Series,
        "advance",
        lambda *a, **k: (_ for _ in ()).throw(service.HostShutdown()),
    )
    with pytest.raises(service.HostShutdown):
        service.tick(repo)

    assert (
        json.loads(service.config_path(repo).read_text())["phase"] == "recovery_pending"
    )
    monkeypatch.setattr(series.Series, "advance", original)
    # When the service resumes, then complete the same ID without repeating on the next tick.
    service.tick(repo)
    service.tick(repo)
    assert len(calls.read_text().splitlines()) == 5
    assert json.loads(service.config_path(repo).read_text())["phase"] == "completed"


def test_managed_pause_survives_service_restart(adaptive_case):
    # Given an operator pause before the first allocation.
    repo, directory, calls = adaptive_case
    service.manage(repo, directory.name)
    (repo / ".mutagi/runs/service.pause").write_text("pause")
    # When launchd invokes the service twice, then no session starts and the pause persists.
    service.tick(repo)
    service.tick(repo)
    assert not calls.exists()
    assert series.Series(directory).state["phase"] == "stopped"
    assert json.loads(service.config_path(repo).read_text())["enabled"] is False


def test_explicit_manage_resumes_operator_pause_with_original_deadline(adaptive_case):
    # Given an operator-paused series that remains paused on automatic startup.
    repo, directory, calls = adaptive_case
    service.manage(repo, directory.name)
    deadline = series.Series(directory).state["deadline"]
    (repo / ".mutagi/runs/service.pause").write_text("pause")
    service.tick(repo)
    assert series.Series(directory).state["phase"] == "stopped"
    # When the operator explicitly manages the same ID again, then continue without extending its deadline.
    service.manage(repo, directory.name)
    service.tick(repo)
    final = series.Series(directory).state
    assert final["phase"] == "completed"
    assert final["deadline"] == deadline
    assert len(calls.read_text().splitlines()) == 5
    assert not (repo / ".mutagi/runs/service.pause").exists()


def test_service_failure_retries_are_bounded(adaptive_case, monkeypatch):
    # Given persistent repository or preservation errors.
    repo, directory, calls = adaptive_case
    service.manage(repo, directory.name)
    monkeypatch.setattr(
        series.Series,
        "advance",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("save unavailable")),
    )
    # When startup repeats, then do not retry the model indefinitely.
    for _ in range(5):
        service.tick(repo)

    config = json.loads(service.config_path(repo).read_text())
    assert config["attempts"] == 3
    assert config["phase"] == "needs_attention"
    assert not config["enabled"]
    assert not calls.exists()


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
    original = runner.Runner.execute

    def measured_fixture(self):
        assigned = self.state.pop("usage_budget")
        assert assigned["tokens"] == 1000
        original(self)  # Run the existing fake CLI and real preservation logic.
        self.state["usage_budget"] = assigned
        self.save()
        if usage_state != "missing":
            runner.atomic_json(
                self.run / "usage.json",
                {
                    "tokens": 1000 if usage_state == "exhausted" else 100,
                    "cost": "0.1",
                    "complete": usage_state == "exhausted",
                },
            )

    monkeypatch.setattr(runner.Runner, "execute", measured_fixture)
    # When the first result is preserved, then neither inheritance nor integration can consume more usage.
    result = controller.advance()
    expected = "usage_exhausted" if usage_state == "exhausted" else "usage_unknown"
    assert result["phase"] == expected
    current = rounds.Round(
        repo / ".mutagi/runs/rounds" / result["rounds"][0]["id"]
    ).state
    assert current["next"] == 1
    assert current["jobs"][1]["allocation"] is None
    assert len(calls.read_text().splitlines()) == 1
    assert series.Series(directory).advance(recovery=True)["phase"] == expected


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
    first = rounds.Round(repo / ".mutagi/runs/rounds" / result["rounds"][0]["id"]).state
    saved = runner.Runner(repo / ".mutagi/runs" / first["jobs"][-1]["id"]).state
    assert saved["reason"] == "invalid_result"
    assert saved["phase"] == "registered"
    assert saved["published"] is False
    assert runner.git(repo, "rev-parse", saved["branch"]) == saved["saved_commit"]


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


def test_service_recovers_after_real_supervisor_sigkill(adaptive_case):
    # Given a managed supervisor killed after the first round is published.
    import os
    import subprocess
    import sys

    repo, directory, calls = adaptive_case
    service.manage(repo, directory.name)
    setup = "import os;from mutagi import run_series,series_service;from pathlib import Path\n"
    failure = (
        setup
        + "original=run_series.Series.save\ndef crash(self):\n original(self)\n if self.state['next']==1:os.kill(os.getpid(),9)\nrun_series.Series.save=crash\n"
        + f"series_service.tick(Path({str(repo)!r}))\n"
    )
    stopped = subprocess.run(
        [sys.executable, "-c", failure],
        env=os.environ,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert stopped.returncode == -9, stopped.stderr
    assert len(calls.read_text().splitlines()) == 3
    before = series.Series(directory).state
    # When a new process performs scheduled recovery, then run only the remaining rounds.
    resumed = subprocess.run(
        [sys.executable, "-c", setup + f"series_service.tick(Path({str(repo)!r}))\n"],
        env=os.environ,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert resumed.returncode == 0, resumed.stderr
    after = series.Series(directory).state
    assert after["phase"] == "completed"
    assert after["deadline"] == before["deadline"]
    assert len(calls.read_text().splitlines()) == 5
    assert json.loads(service.config_path(repo).read_text())["attempts"] == 2


@pytest.mark.parametrize("interrupt", [False, True])
def test_owned_server_protocol_records_final_or_interrupts(
    tmp_path, fake_codex, interrupt
):
    # Given the documented App Server protocol and a strict usage threshold.
    import sys
    import time

    from mutagi import codex_session

    fake_codex.write_text(f"""#!{sys.executable}
import sys,json
for line in sys.stdin:
 m=json.loads(line);method=m['method'];p=m.get('params',{{}})
 def emit(x):print(json.dumps(x),flush=True)
 if method=='initialize':emit({{'id':m['id'],'result':{{}}}})
 elif method=='thread/start':emit({{'id':m['id'],'result':{{'thread':{{'id':'owned-thread'}},'model':'gpt-6-astra'}}}})
 elif method=='turn/start':
  emit({{'id':m['id'],'result':{{'turn':{{'id':'owned-turn'}}}}}})
  emit({{'method':'thread/tokenUsage/updated','params':{{'threadId':'owned-thread','turnId':'owned-turn','tokenUsage':{{'total':{{'inputTokens':1000,'cachedInputTokens':0,'outputTokens':10,'reasoningOutputTokens':0,'totalTokens':1010}}}}}}}})
  if not {interrupt!r}:
   emit({{'method':'item/completed','params':{{'item':{{'type':'agentMessage','phase':'final_answer','text':'{{"done":true}}'}}}}}})
   emit({{'method':'turn/completed','params':{{'turn':{{'id':'owned-turn','status':'completed'}}}}}})
 elif method=='turn/interrupt':
  emit({{'id':m['id'],'result':{{}}}})
  emit({{'method':'turn/completed','params':{{'turn':{{'id':'owned-turn','status':'interrupted'}}}}}})
""")
    run = tmp_path / "owned"
    run.mkdir()
    runner.atomic_json(
        run / "state.json",
        {
            "model": "gpt-6-astra",
            "effort": "medium",
            "worktree": str(run),
            "exploration_deadline": time.time() + 15,
            "usage_budget": {
                "tokens": 1 if interrupt else 10000,
                "cost": 10,
                "rates": RATES,
            },
        },
    )
    runner.atomic_json(run / "schema.json", {"type": "object"})
    (run / "prompt.txt").write_text("Protocol test")
    # When one turn runs, then interrupted usage is not reported as a complete cost record.
    code = codex_session.execute(run)
    usage = json.loads((run / "usage.json").read_text())
    assert code == (75 if interrupt else 0)
    assert usage["complete"] is not interrupt
    assert usage["tokens"] == 1010
    assert (run / "result.json").exists() is not interrupt
