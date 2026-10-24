"""Verify complete onboarding through the public command paths."""

import argparse
import io
import json
from pathlib import Path
import sys
import time

import pytest

from mutagi import (
    command_job,
    initialize,
    run_experiment,
    run_round,
    run_series,
    series_service,
    status,
)
from mutagi.inputs import add_plan_arguments, read_plan
from mutagi.project import parse_project
from mutagi.repository import git, project_at
from test_onboarding import commit, empty_repo as empty_repo, plan_for, snapshot
from test_runner import repo as repo


def plan_args(kind, *extra):
    parser = argparse.ArgumentParser()
    add_plan_arguments(parser, series=kind == "series")
    flags = [
        "--base",
        "HEAD",
        "--model",
        "selected-model",
        "--effort",
        "high",
        "--seconds",
        "600",
        "--experiment-seconds",
        "60",
        "--integration-seconds",
        "60",
        "--reserve-seconds",
        "10",
    ]
    if kind == "series":
        flags += ["--round-seconds", "300"]
    return parser, flags + list(extra)


@pytest.mark.parametrize("kind,module", [("round", run_round), ("series", run_series)])
def test_cli_task_order_and_plan_round_trip(kind, module, tmp_path):
    parser, flags = plan_args(
        kind,
        "--prompt",
        "first",
        "--inherit",
        "0",
        "second",
        "--prompt",
        "third",
        "--inherit",
        "1",
        "fourth",
    )
    if kind == "series":
        flags += ["--rounds", "2"]
    plan = read_plan(parser.parse_args(flags), module.validate_plan)
    tasks = plan["tasks"] if kind == "round" else plan["rounds"][0]["tasks"]
    assert [(task["prompt"], task["parent"]) for task in tasks] == [
        ("first", None),
        ("second", 0),
        ("third", None),
        ("fourth", 1),
    ]
    path = tmp_path / "resolved.json"
    path.write_text(json.dumps(plan))
    assert (
        read_plan(parser.parse_args(["--plan", str(path)]), module.validate_plan)
        == plan
    )
    # Reusing a parser must not retain tasks from an earlier parse.
    _, again = plan_args(kind, "--prompt", "only")
    if kind == "series":
        again += ["--rounds", "2"]
    another = read_plan(parser.parse_args(again), module.validate_plan)
    one = another["tasks"] if kind == "round" else another["rounds"][0]["tasks"]
    assert one == [{"prompt": "only", "parent": None}]
    if kind == "series":
        another["rounds"][0]["tasks"][0]["prompt"] = "changed"
        assert another["rounds"][1]["tasks"][0]["prompt"] == "only"


@pytest.mark.parametrize(
    "extra",
    [
        ["--inherit", "0", "first"],
        ["--prompt", "first", "--inherit", "1", "self"],
        ["--prompt", "first", "--inherit", "-1", "negative"],
        ["--prompt", " "],
    ],
)
def test_invalid_cli_tasks_are_rejected(extra):
    parser, flags = plan_args("round", *extra)
    with pytest.raises(ValueError, match="earlier parent"):
        read_plan(parser.parse_args(flags), run_round.validate_plan)


@pytest.mark.parametrize(
    "extra", [["--prompt", "x"], ["--inherit", "0", "x"], ["--rounds", "2"]]
)
def test_plan_and_task_inputs_cannot_be_mixed(extra, tmp_path):
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan_for("series")))
    parser, _ = plan_args("series")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="cannot be combined"):
        read_plan(
            parser.parse_args(["--plan", str(path), *extra]), run_series.validate_plan
        )
    assert path.read_bytes() == before


@pytest.mark.parametrize("rounds", [[], ["--rounds", "0"], ["--rounds", "1"]])
def test_series_requires_explicit_finite_round_count(rounds):
    parser, flags = plan_args("series", "--prompt", "x", *rounds)
    with pytest.raises(ValueError, match="at least two"):
        read_plan(parser.parse_args(flags), run_series.validate_plan)


@pytest.mark.parametrize(
    "kind,module",
    [("round", run_round), ("series", run_series), ("service", series_service)],
)
def test_plan_preview_is_read_only_and_round_trips(
    repo, monkeypatch, capsys, kind, module
):
    plan_kind = "series" if kind == "service" else kind
    _, flags = plan_args(plan_kind, "--prompt", "first", "--inherit", "0", "next")
    if plan_kind == "series":
        flags += ["--rounds", "2"]
    before = snapshot(repo)
    monkeypatch.setattr(
        sys,
        "argv",
        [f"mutagi {kind}", "--repo", str(repo), "start", *flags, "--dry-run"],
    )
    module.main()
    resolved = json.loads(capsys.readouterr().out)
    assert resolved["base"] == git(repo, "rev-parse", "HEAD")
    assert snapshot(repo) == before
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(resolved)))
    monkeypatch.setattr(
        sys,
        "argv",
        [f"mutagi {kind}", "--repo", str(repo), "start", "--plan", "-", "--dry-run"],
    )
    module.main()
    assert json.loads(capsys.readouterr().out) == resolved
    assert snapshot(repo) == before


def write_defaults(repo):
    path = repo / ".mutagi/config.toml"
    path.write_text(
        path.read_text()
        + """
[execution]
model = "common-model"
effort = "medium"
[execution.experiment]
seconds = 90
reserve_seconds = 15
[execution.round]
model = "round-model"
seconds = 600
experiment_seconds = 60
integration_seconds = 60
reserve_seconds = 10
[execution.series]
seconds = 900
round_seconds = 300
experiment_seconds = 60
integration_seconds = 60
reserve_seconds = 10
"""
    )
    commit(repo)
    return git(repo, "rev-parse", "HEAD")


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
            "--prompt",
            "case",
            "--dry-run",
        ],
    )
    run_round.main()
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
    run_round.main()
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
    run_round.main()
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
    run_experiment.main()
    preview = json.loads(capsys.readouterr().out)
    assert snapshot(repo) == before
    assert preview["model"] == "common-model"
    assert preview["seconds"] == 90 and preview["reserve_seconds"] == 15
    monkeypatch.setattr(
        run_experiment.Runner, "advance", lambda self, recovery: self.state
    )
    monkeypatch.setattr(sys, "argv", flags)
    run_experiment.main()
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
    with pytest.raises(ValueError, match="--model and --effort"):
        run_experiment.main()
    monkeypatch.setattr(
        sys,
        "argv",
        [*base, "--model", "chosen", "--effort", "medium", "--parent-id", "missing"],
    )
    with pytest.raises(ValueError, match="both --parent"):
        run_experiment.main()
    assert snapshot(repo) == before


@pytest.mark.parametrize(
    "defaults",
    [
        'execution = "model"',
        '[execution]\nmodel = " "',
        '[execution]\neffort = "guess"',
        '[execution]\nunknown = "x"',
        "[execution.round]\nseconds = nan",
        "[execution.experiment]\nseconds = true",
        "[execution.experiment]\nround_seconds = 60",
        "[execution.series]\nrounds = 2",
    ],
)
def test_invalid_execution_defaults_are_actionable(defaults):
    base = Path(__file__).with_name("project.toml").read_text()
    text = (
        defaults + "\n" + base
        if defaults.startswith("execution =")
        else base + "\n" + defaults
    )
    with pytest.raises(ValueError, match="execution"):
        parse_project(text)


@pytest.mark.parametrize("preset", list(initialize.PRESETS))
def test_explicit_preparation_presets_and_model_defaults(empty_repo, preset):
    for name in initialize.PRESETS[preset]["requires"]:
        (empty_repo / name).write_text("Project manifest fixture\n")
    before = snapshot(empty_repo)
    options = dict(preset=preset, execution={"model": "chosen-model", "effort": "high"})
    initialize.initialize(empty_repo, None, [], [], dry_run=True, **options)
    assert snapshot(empty_repo) == before
    initialize.initialize(empty_repo, None, [], [], **options)
    config = project_at(empty_repo)
    assert config["preparation"] == initialize.PRESETS[preset]["commands"]
    assert config["execution"] == {"model": "chosen-model", "effort": "high"}
    assert config["exploration_paths"] == ["knowledge/notes/"]


def test_preset_missing_manifest_rejects_before_writing(empty_repo):
    before = snapshot(empty_repo)
    with pytest.raises(ValueError, match="regular file"):
        initialize.initialize(empty_repo, None, [], [], preset="node-npm")
    assert snapshot(empty_repo) == before


class Terminal(io.StringIO):
    def isatty(self):
        return True


@pytest.mark.parametrize("confirmation", ["yes", "no"])
def test_interactive_initialization_previews_and_respects_choice(
    empty_repo, monkeypatch, capsys, confirmation
):
    answers = iter(
        ["", "python-stdlib", "src/", "", "", "selected-model", "medium", confirmation]
    )
    before = snapshot(empty_repo)
    monkeypatch.setattr(sys, "stdin", Terminal())
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    monkeypatch.setattr(
        sys, "argv", ["mutagi init", "--repo", str(empty_repo), "--interactive"]
    )
    initialize.main()
    output = capsys.readouterr().out
    assert 'model = "selected-model"' in output
    assert "--- .mutagi/config.toml ---" in output
    if confirmation == "yes":
        assert (
            project_at(empty_repo)["preparation"]
            == initialize.PRESETS["python-stdlib"]["commands"]
        )
    else:
        assert snapshot(empty_repo) == before


def test_interactive_dry_run_and_eof_leave_no_files(empty_repo, monkeypatch):
    before = snapshot(empty_repo)
    monkeypatch.setattr(sys, "stdin", Terminal())
    answers = iter(["", "", "", "", "", ""])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    monkeypatch.setattr(
        sys,
        "argv",
        ["mutagi init", "--repo", str(empty_repo), "--interactive", "--dry-run"],
    )
    initialize.main()
    assert snapshot(empty_repo) == before

    def eof(prompt):
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)
    with pytest.raises(SystemExit):
        initialize.main()
    assert snapshot(empty_repo) == before


def test_interactive_rejects_nonterminal_input(empty_repo, monkeypatch):
    before = snapshot(empty_repo)
    monkeypatch.setattr(sys, "stdin", io.StringIO())
    monkeypatch.setattr(
        sys, "argv", ["mutagi init", "--repo", str(empty_repo), "--interactive"]
    )
    with pytest.raises(ValueError, match="requires a terminal"):
        initialize.main()
    assert snapshot(empty_repo) == before


@pytest.mark.parametrize(
    "kind,module,creator",
    [
        ("round", run_round, run_round.create_round),
        ("series", run_series, run_series.create_series),
    ],
)
def test_status_shows_progress_and_preserves_raw_state(
    repo, monkeypatch, capsys, kind, module, creator
):
    plan = {**plan_for(kind), "model": "chosen-model"}
    directory = creator(repo, plan)
    before = snapshot(repo)
    command = [f"mutagi {kind}", "--repo", str(repo), "status", directory.name]
    monkeypatch.setattr(sys, "argv", command)
    module.main()
    output = capsys.readouterr().out
    assert "Recorded phase: running" in output
    assert "Recorded progress: 0/" in output
    assert "Human adoption: not tracked" in output
    assert f"resume {directory.name}" in output
    monkeypatch.setattr(sys, "argv", [*command, "--json"])
    module.main()
    assert capsys.readouterr().out == (directory / "state.json").read_text()
    assert snapshot(repo) == before


def test_experiment_status_distinguishes_observation_preservation_and_adoption(
    repo, monkeypatch, capsys
):
    with run_experiment.lock(repo):
        directory = run_experiment.create(
            repo, "HEAD", "case", "chosen-model", "medium", 60, 10
        )
    state = json.loads((directory / "state.json").read_text())
    state.update(
        phase="registered",
        saved_commit=state["base"],
        result={
            "status": "refuted",
            "summary": "Counterexample",
            "limits": "Finite inputs",
            "record": "experiments/one/record.md",
        },
    )
    run_experiment.atomic_json(directory / "state.json", state)
    before = snapshot(repo)
    monkeypatch.setattr(
        sys,
        "argv",
        ["mutagi experiment", "--repo", str(repo), "status", directory.name],
    )
    run_experiment.main()
    output = capsys.readouterr().out
    assert "Recorded phase: registered" in output
    assert "Reported observation: refuted" in output
    assert "Saved revision:" in output
    assert "Human adoption: not tracked" in output
    assert "git -C" in output
    monkeypatch.setattr(
        sys,
        "argv",
        ["mutagi experiment", "--repo", str(repo), "status", directory.name, "--json"],
    )
    run_experiment.main()
    assert json.loads(capsys.readouterr().out) == state
    assert snapshot(repo) == before


def test_nested_status_uses_owner_for_recovery(repo, capsys):
    plan = {**plan_for("round"), "model": "chosen"}
    directory = run_round.create_round(repo, plan)
    state = json.loads((directory / "state.json").read_text())
    child_id = state["jobs"][0]["id"]
    with run_experiment.lock(repo):
        run_experiment.create(
            repo, "HEAD", "case", "chosen", "medium", 60, 10, identifier=child_id
        )
    status.show_execution(repo, "experiment", child_id)
    output = capsys.readouterr().out
    assert f"mutagi round --repo {repo} resume {directory.name}" in output
    assert f"experiment --repo {repo} resume" not in output
    status.show_execution(repo, "round", directory.name)
    assert "Current child details:" in capsys.readouterr().out


def test_process_observation_checks_identity_and_system_boot(monkeypatch):
    process = {
        "pid": 42,
        "identity": "original",
        "boot_identity": "boot",
        "children": [{"pid": 43, "identity": "child"}],
    }
    monkeypatch.setattr(status, "boot_identity", lambda: "boot")
    monkeypatch.setattr(
        status,
        "processes",
        lambda: {42: {"identity": "reused"}, 43: {"identity": "child"}},
    )
    assert (
        status.observed_processes({"process": process})
        == "Recorded managed processes are present: 43"
    )
    monkeypatch.setattr(status, "boot_identity", lambda: "new-boot")
    assert "earlier system boot" in status.observed_processes({"process": process})


def test_job_status_read_only_and_raw_json(repo, monkeypatch, capsys):
    directory = repo / ".mutagi/runs/job-test"
    directory.mkdir(parents=True)
    state = {
        "id": "job-id",
        "cwd": str(repo),
        "command": ["echo", "hello"],
        "stage": "finished",
        "deadline": time.time(),
        "process": None,
        "supervisor": None,
        "result": {"exit_code": 0, "timed_out": False},
    }
    command_job.save_state(directory, state)
    before = snapshot(repo)
    monkeypatch.setattr(command_job, "processes", lambda: {})
    monkeypatch.setattr(sys, "argv", ["mutagi job", "status", str(directory)])
    command_job.main()
    output = capsys.readouterr().out
    assert "Recorded stage: finished" in output and "Command log:" in output
    monkeypatch.setattr(sys, "argv", ["mutagi job", "status", str(directory), "--json"])
    command_job.main()
    assert json.loads(capsys.readouterr().out) == state
    assert snapshot(repo) == before


def test_service_status_preserves_service_and_series_checkpoints(
    repo, monkeypatch, capsys
):
    plan = {**plan_for("series"), "model": "chosen"}
    directory = run_series.create_series(repo, plan)
    series_service.manage(repo, directory.name)
    before = snapshot(repo)
    command = ["mutagi service", "--repo", str(repo), "status"]
    monkeypatch.setattr(sys, "argv", command)
    series_service.main()
    output = capsys.readouterr().out
    assert "Service recorded phase: pending" in output
    assert "Recorded progress:" in output
    assert "does not prove" in output
    monkeypatch.setattr(sys, "argv", [*command, "--json"])
    series_service.main()
    assert json.loads(capsys.readouterr().out) == json.loads(
        series_service.config_path(repo).read_text()
    )
    assert snapshot(repo) == before


def test_app_handoff_status_does_not_suggest_cli_resume(repo, capsys):
    with run_experiment.lock(repo):
        directory = run_experiment.create(
            repo, "HEAD", "case", "chosen", "medium", 60, 10
        )
    state = json.loads((directory / "state.json").read_text())
    state.update(phase="awaiting_app", thread_id="assigned-app-thread")
    run_experiment.atomic_json(directory / "state.json", state)
    before = snapshot(repo)
    status.show_execution(repo, "experiment", directory.name)
    output = capsys.readouterr().out
    assert "Assigned App thread: assigned-app-thread" in output
    assert "mutagi handoff" in output and "--app-observation" in output
    assert "resume" not in output
    assert snapshot(repo) == before
