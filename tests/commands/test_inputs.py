from __future__ import annotations

import io
import json
import sys

from tests.support.cli import invoke

import pytest

import mutagi.commands.check as check
import mutagi.commands.experiment as experiment_command
import mutagi.commands.round as round_command
import mutagi.commands.series as series_command
import mutagi.execution.experiment as experiment
import mutagi.execution.round as round_controller
import mutagi.execution.series as series
from typer import BadParameter
from typer.testing import CliRunner

from mutagi.cli import app
from mutagi.commands.inputs import Step, read_plan
from mutagi.project.repository import git
from tests.support.plans import plan_args, plan_for


@pytest.mark.parametrize("kind", ["round", "series"])
def test_cli_task_order_and_plan_round_trip(kind, repo, tmp_path):
    runner = CliRunner()
    flags = plan_args(
        kind,
        "--step",
        "none: first",
        "--step",
        "0: second",
        "--step",
        "none: third",
        "--step",
        "1: fourth",
    )
    if kind == "series":
        flags += ["--rounds", "2"]
    command = [kind, "--repo", str(repo), "start"]
    result = runner.invoke(app, [*command, *flags, "--dry-run"])
    assert result.exit_code == 0, result.output
    plan = json.loads(result.stdout)
    tasks = plan["tasks"] if kind == "round" else plan["rounds"][0]["tasks"]
    assert [(task["prompt"], task["parent"]) for task in tasks] == [
        ("first", None),
        ("second", 0),
        ("third", None),
        ("fourth", 1),
    ]
    path = tmp_path / "resolved.json"
    path.write_text(json.dumps(plan))
    result = runner.invoke(app, [*command, "--plan", str(path), "--dry-run"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == plan
    again = plan_args(kind, "--step", "none: only")
    if kind == "series":
        again += ["--rounds", "2"]
    result = runner.invoke(app, [*command, *again, "--dry-run"])
    assert result.exit_code == 0, result.output
    another = json.loads(result.stdout)
    one = another["tasks"] if kind == "round" else another["rounds"][0]["tasks"]
    assert one == [{"prompt": "only", "parent": None}]
    if kind == "series":
        another = read_plan(
            "series", plan=None, steps=[Step("only", None)], rounds=2, overrides={}
        )
        another["rounds"][0]["tasks"][0]["prompt"] = "changed"
        assert another["rounds"][1]["tasks"][0]["prompt"] == "only"
    assert not (repo / ".mutagi/runs").exists()


@pytest.mark.parametrize(
    "extra",
    [
        ["--step", "0: first"],
        ["--step", "none: first", "--step", "1: self"],
        ["--step", "none: first", "--step", "-1: negative"],
        ["--step", "none: "],
        ["--step", "wrong"],
        ["--step", "parent: text"],
    ],
)
def test_invalid_cli_tasks_are_rejected(extra, repo):
    result = CliRunner().invoke(
        app,
        [
            "round",
            "--repo",
            str(repo),
            "start",
            *plan_args("round", *extra),
            "--dry-run",
        ],
    )
    assert result.exit_code == 2, result.output
    assert not result.stdout and "Error" in result.stderr
    assert not (repo / ".mutagi/runs").exists()


@pytest.mark.parametrize(
    "steps,rounds", [([Step("x", None)], None), ([Step("x", 0)], None), ([], 2)]
)
def test_plan_and_task_inputs_cannot_be_mixed(steps, rounds, tmp_path):
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan_for("series")))
    before = path.read_bytes()
    with pytest.raises(ValueError, match="cannot be combined"):
        read_plan("series", plan=str(path), steps=steps, rounds=rounds, overrides={})
    assert path.read_bytes() == before


@pytest.mark.parametrize("rounds", [[], ["--rounds", "0"], ["--rounds", "1"]])
def test_series_requires_explicit_finite_round_count(rounds, repo):
    result = CliRunner().invoke(
        app,
        [
            "series",
            "--repo",
            str(repo),
            "start",
            *plan_args("series", "--step", "none: x", *rounds),
            "--dry-run",
        ],
    )
    assert result.exit_code == 2, result.output
    assert not (repo / ".mutagi/runs").exists()


@pytest.mark.parametrize(
    "kind,module,controller",
    [
        ("round", round_command, round_controller.Round),
        ("series", series_command, series.Series),
    ],
)
@pytest.mark.parametrize("source", ["file", "stdin"])
def test_plan_overrides_are_validated_and_saved_before_execution(
    repo, tmp_path, monkeypatch, kind, module, controller, source
):
    plan = plan_for(kind)
    original = json.dumps(plan, indent=2)
    path = tmp_path / "plan.json"
    path.write_text(original)
    monkeypatch.setattr(sys, "stdin", io.StringIO(original))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            f"mutagi {kind}",
            "--repo",
            str(repo),
            "start",
            "--plan",
            str(path) if source == "file" else "-",
            "--model",
            "chosen-model",
            "--seconds",
            "900",
        ],
    )
    monkeypatch.setattr(controller, "advance", lambda self, recovery: self.state)
    invoke(module)
    states = list(
        (repo / ".mutagi/runs" / ("rounds" if kind == "round" else "series")).glob(
            "*/state.json"
        )
    )
    assert len(states) == 1
    saved = json.loads(states[0].read_text())["plan"]
    assert saved["model"] == "chosen-model"
    assert saved["seconds"] == 900
    assert saved["base"] == git(repo, "rev-parse", "HEAD")
    assert saved["experiment_seconds"] == plan["experiment_seconds"]
    assert path.read_text() == original
    with pytest.raises(ValueError, match=f"mutagi {kind}.*resume"):
        check.unfinished_work(repo)


@pytest.mark.parametrize(
    "kind,module", [("round", round_command), ("series", series_command)]
)
def test_missing_model_fails_before_allocating_any_state(
    repo, monkeypatch, kind, module
):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(plan_for(kind))))
    monkeypatch.setattr(
        sys, "argv", [f"mutagi {kind}", "--repo", str(repo), "start", "--plan", "-"]
    )
    with pytest.raises(BadParameter, match="schema"):
        invoke(module)
    assert not (repo / ".mutagi/runs").exists()


def test_model_override_rejects_incompatible_usage_rates(repo, monkeypatch):
    plan = plan_for("series")
    plan.update(
        model="original-model",
        usage_limits={
            "tokens": 1000,
            "cost": 1,
            "rates": {
                "model": "original-model",
                "input": 1,
                "cached_input": 1,
                "output": 1,
                "unit": "USD",
                "source": "Test rate card",
            },
        },
    )
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(plan)))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "mutagi series",
            "--repo",
            str(repo),
            "start",
            "--plan",
            "-",
            "--model",
            "different-model",
        ],
    )
    with pytest.raises(BadParameter, match="rate card model"):
        invoke(series_command)
    assert not (repo / ".mutagi/runs").exists()


@pytest.mark.parametrize("source", ["prompt", "file", "stdin"])
@pytest.mark.parametrize(
    "text",
    [
        "  Compare one assumption.\n日本語の入力も保存する。\n",
        "Keep line endings.\r\nAnd trailing whitespace.  \r\n",
    ],
)
def test_exploration_inputs_preserve_the_same_text(
    repo, tmp_path, monkeypatch, source, text
):
    path = tmp_path / "task.txt"
    path.write_text(text)
    argument = (
        ["--prompt", text]
        if source == "prompt"
        else ["--task", str(path) if source == "file" else "-"]
    )
    monkeypatch.setattr(sys, "stdin", io.StringIO(text))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "mutagi experiment",
            "--repo",
            str(repo),
            "start",
            "--base",
            "HEAD",
            *argument,
            "--model",
            "chosen-model",
            "--effort",
            "medium",
        ],
    )
    monkeypatch.setattr(experiment.Runner, "advance", lambda self, recovery: self.state)
    invoke(experiment_command)
    saved = list((repo / ".mutagi/runs").glob("*/task.txt"))
    assert len(saved) == 1
    assert saved[0].read_bytes().decode("utf-8") == text


@pytest.mark.parametrize(
    "instruction,exception",
    [
        (["--prompt", " \n "], BadParameter),
        (["--task", "missing-task.txt"], BadParameter),
        (["--prompt", "text", "--task", "-"], BadParameter),
    ],
)
def test_invalid_instruction_does_not_allocate_state(
    repo, monkeypatch, instruction, exception
):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "mutagi experiment",
            "--repo",
            str(repo),
            "start",
            "--base",
            "HEAD",
            *instruction,
            "--model",
            "chosen-model",
            "--effort",
            "medium",
        ],
    )
    with pytest.raises(exception):
        invoke(experiment_command)
    assert not (repo / ".mutagi/runs").exists()
