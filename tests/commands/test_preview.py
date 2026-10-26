from __future__ import annotations

import io
import json
import sys

from tests.support.cli import invoke

import pytest
from typer import BadParameter

import mutagi.commands.experiment as experiment_command
import mutagi.commands.round as round_command
import mutagi.commands.series as series_command
import mutagi.commands.service as service_command
import mutagi.execution.experiment as experiment
from mutagi.project.repository import git
from tests.support.plans import plan_args
from tests.support.repositories import commit, snapshot, write_defaults


@pytest.mark.parametrize(
    "kind,module",
    [
        ("round", round_command),
        ("series", series_command),
        ("service", service_command),
    ],
)
def test_plan_preview_is_read_only_and_round_trips(
    repo, monkeypatch, capsys, kind, module
):
    plan_kind = "series" if kind == "service" else kind
    flags = plan_args(plan_kind, "--step", "none: first", "--step", "0: next")
    if plan_kind == "series":
        flags += ["--rounds", "2"]
    before = snapshot(repo)
    monkeypatch.setattr(
        sys,
        "argv",
        [f"mutagi {kind}", "--repo", str(repo), "start", *flags, "--dry-run"],
    )
    invoke(module)
    resolved = json.loads(capsys.readouterr().out)
    assert resolved["base"] == git(repo, "rev-parse", "HEAD")
    assert snapshot(repo) == before
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(resolved)))
    monkeypatch.setattr(
        sys,
        "argv",
        [f"mutagi {kind}", "--repo", str(repo), "start", "--plan", "-", "--dry-run"],
    )
    invoke(module)
    assert json.loads(capsys.readouterr().out) == resolved
    assert snapshot(repo) == before


def test_defaults_come_from_selected_commit_and_explicit_values_win(
    repo, monkeypatch, capsys, tmp_path
):
    chosen = write_defaults(repo)
    path = repo / ".mutagi/config.toml"
    path.write_text(
        path.read_text().replace('model = "round-model"', 'model = "new-model"')
    )
    commit(repo)
    path.write_text(
        path.read_text().replace('model = "new-model"', 'model = "uncommitted-model"')
    )
    before = snapshot(repo)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "mutagi round",
            "--repo",
            str(repo),
            "start",
            "--base",
            chosen,
            "--step",
            "none: case",
            "--dry-run",
        ],
    )
    invoke(round_command)
    result = json.loads(capsys.readouterr().out)
    assert result["model"] == "round-model"
    assert result["effort"] == "medium"
    assert result["base"] == chosen
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "base": chosen,
                "model": "plan-model",
                "tasks": [{"prompt": "case", "parent": None}],
            }
        )
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "mutagi round",
            "--repo",
            str(repo),
            "start",
            "--plan",
            str(plan),
            "--dry-run",
        ],
    )
    invoke(round_command)
    assert json.loads(capsys.readouterr().out)["model"] == "plan-model"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "mutagi round",
            "--repo",
            str(repo),
            "start",
            "--plan",
            str(plan),
            "--model",
            "cli-model",
            "--effort",
            "high",
            "--dry-run",
        ],
    )
    invoke(round_command)
    resolved = json.loads(capsys.readouterr().out)
    assert (resolved["model"], resolved["effort"]) == ("cli-model", "high")
    assert snapshot(repo) == before


def test_experiment_preview_and_execution_use_same_saved_defaults(
    repo, monkeypatch, capsys
):
    chosen = write_defaults(repo)
    flags = [
        "mutagi experiment",
        "--repo",
        str(repo),
        "start",
        "--base",
        chosen,
        "--prompt",
        "Text\r\nwith whitespace.  \n",
    ]
    before = snapshot(repo)
    monkeypatch.setattr(sys, "argv", [*flags, "--dry-run"])
    invoke(experiment_command)
    preview = json.loads(capsys.readouterr().out)
    assert snapshot(repo) == before
    assert preview["model"] == "common-model"
    assert preview["seconds"] == 90 and preview["reserve_seconds"] == 15
    monkeypatch.setattr(experiment.Runner, "advance", lambda self, recovery: self.state)
    monkeypatch.setattr(sys, "argv", flags)
    invoke(experiment_command)
    state_path = next((repo / ".mutagi/runs").glob("*/state.json"))
    state = json.loads(state_path.read_text())
    for key in ("base", "model", "effort", "seconds", "reserve_seconds"):
        assert state[key] == preview[key]
    assert (state_path.parent / "task.txt").read_bytes().decode() == preview["prompt"]


def test_experiment_preview_rejects_missing_model_and_bad_parent_without_writes(
    repo, monkeypatch
):
    base = [
        "mutagi experiment",
        "--repo",
        str(repo),
        "start",
        "--base",
        "HEAD",
        "--prompt",
        "case",
        "--dry-run",
    ]
    before = snapshot(repo)
    monkeypatch.setattr(sys, "argv", base)
    with pytest.raises(BadParameter, match="--model and --effort"):
        invoke(experiment_command)
    monkeypatch.setattr(
        sys,
        "argv",
        [*base, "--model", "chosen", "--effort", "medium", "--parent-id", "missing"],
    )
    with pytest.raises(BadParameter, match="both --parent"):
        invoke(experiment_command)
    assert snapshot(repo) == before
