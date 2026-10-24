"""Exercise project generation, diagnostics, and persisted command inputs."""

import argparse
from importlib.metadata import version
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from mutagi import check, run_experiment, run_round, run_series
from mutagi.initialize import initialize
from mutagi.inputs import add_plan_arguments, read_plan
from mutagi.project import parse_project
from mutagi.prompts import record_template
from mutagi.repository import git, project_at
from test_runner import repo as repo


@pytest.fixture
def empty_repo(tmp_path):
    root = tmp_path / "sample"
    subprocess.run(
        ["git", "init", "-b", "main", str(root)], check=True, capture_output=True
    )
    git(root, "config", "user.name", "Test")
    git(root, "config", "user.email", "test@example.invalid")
    return root


def snapshot(root):
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def commit(root):
    git(root, "add", ".")
    git(root, "commit", "-m", "test: configure project")


@pytest.fixture
def diagnostic_codex(tmp_path, monkeypatch):
    binary = tmp_path / "diagnostic-bin"
    binary.mkdir()
    command = binary / "codex"
    command.write_text(f"""#!{sys.executable}
import os, sys
args = sys.argv[1:]
if args == ["--version"]:
    print("codex-cli test")
elif args == ["exec", "--help"]:
    print("--json --approve-for-me --model --config --output-schema --output-last-message")
elif args == ["login", "status"]:
    print("private-auth-output-for-test")
    sys.exit(int(os.environ.get("TEST_AUTH_STATUS", "0")))
else:
    raise AssertionError("Diagnostics must not execute a model: " + repr(args))
""")
    command.chmod(0o755)
    monkeypatch.setenv("PATH", str(binary) + os.pathsep + os.environ["PATH"])
    return command


def test_default_initialization_can_prepare_a_committed_project(empty_repo):
    initialize(empty_repo, None, [], [])
    config = project_at(empty_repo)
    assert config["name"] == "sample"
    assert config["exploration_paths"] == ["knowledge/notes/"]
    assert config["references"] == config["preparation"] == []
    assert not (empty_repo / ".mutagi/plans").exists()
    assert not (empty_repo / ".mutagi/runs").exists()
    commit(empty_repo)
    with run_experiment.lock(empty_repo):
        directory = run_experiment.create(
            empty_repo, "HEAD", "Test one assumption.", "test-model", "medium", 60, 10
        )
    job = run_experiment.Runner(directory)
    job.prepare()
    assert job.state["phase"] == "ready"
    assert record_template() in job.prompt()
    assert git(empty_repo, "status", "--porcelain") == ""


def test_init_dry_run_and_explicit_paths_preserve_existing_files(empty_repo, capsys):
    (empty_repo / "README.md").write_text("# Input\n")
    ignore = empty_repo / ".gitignore"
    original = b"# Keep this comment\r\ncache/\r\n.mutagi/runs/"
    ignore.write_bytes(original)
    before = snapshot(empty_repo)
    initialize(
        empty_repo,
        'A "quoted" project',
        ["lib/", "tests/"],
        ["README.md"],
        dry_run=True,
    )
    assert snapshot(empty_repo) == before
    assert ".mutagi/config.toml" in capsys.readouterr().out
    initialize(empty_repo, 'A "quoted" project', ["lib/", "tests/"], ["README.md"])
    config = project_at(empty_repo)
    assert config["name"] == 'A "quoted" project'
    assert config["exploration_paths"] == ["knowledge/notes/", "lib/", "tests/"]
    assert config["references"] == ["README.md"]
    assert ignore.read_bytes() == original + b"\n/.mutagi/worktrees/\n"
    before = snapshot(empty_repo)
    with pytest.raises(ValueError, match="mutagi check"):
        initialize(empty_repo, None, [], [])
    assert snapshot(empty_repo) == before


@pytest.mark.parametrize(
    "name",
    [
        ".mutagi/config.toml",
        ".mutagi/prompts/inherit.md",
        "experiments/INDEX.md",
        "knowledge/README.md",
        ".mutagi",
    ],
)
def test_initializer_checks_all_collisions_before_writing(empty_repo, name):
    path = empty_repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("Existing user content")
    before = snapshot(empty_repo)
    with pytest.raises(ValueError, match="No files were changed"):
        initialize(empty_repo, None, [], [])
    assert snapshot(empty_repo) == before


@pytest.mark.parametrize("name", [".mutagi", "knowledge", ".gitignore"])
def test_initializer_rejects_symlinked_output(empty_repo, tmp_path, name):
    outside = tmp_path / "outside"
    if name == ".gitignore":
        outside.write_text("unrelated")
    else:
        outside.mkdir()
        (outside / "keep").write_text("unrelated")
    (empty_repo / name).symlink_to(outside)
    before = snapshot(empty_repo)
    with pytest.raises(ValueError, match="symlink"):
        initialize(empty_repo, None, [], [])
    assert snapshot(empty_repo) == before
    assert not (empty_repo / "experiments").exists()


def test_init_requires_repository_root_and_existing_references(empty_repo):
    child = empty_repo / "child"
    child.mkdir()
    before = snapshot(empty_repo)
    with pytest.raises(ValueError, match="repository root"):
        initialize(child, None, [], [])
    with pytest.raises(ValueError, match="regular file"):
        initialize(empty_repo, None, [], ["missing.md"])
    with pytest.raises(ValueError, match="project-relative"):
        initialize(empty_repo, None, ["../outside"], [])
    assert snapshot(empty_repo) == before


@pytest.mark.parametrize(
    "replacement",
    ["references = [42]", 'references = "README.md"', 'references = ["../outside"]'],
)
def test_invalid_configuration_identifies_file_and_field(replacement):
    text = Path(__file__).with_name("project.toml").read_text()
    text = text.replace('references = ["AGENTS.md", "docs/operations.md"]', replacement)
    with pytest.raises(ValueError, match=r"\.mutagi/config.toml") as caught:
        parse_project(text)
    assert "references" in str(caught.value)
    with pytest.raises(ValueError, match="missing required field"):
        parse_project('name = "incomplete"')


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


def plan_for(kind):
    plan = {
        "base": "HEAD",
        "effort": "medium",
        "seconds": 600,
        "experiment_seconds": 60,
        "integration_seconds": 60,
        "reserve_seconds": 10,
        "tasks": [{"prompt": "Check a finite case.", "parent": None}],
    }
    if kind == "series":
        tasks = plan.pop("tasks")
        plan.update(round_seconds=300, rounds=[{"tasks": tasks}, {"tasks": tasks}])
    return plan


@pytest.mark.parametrize(
    "kind,module,controller",
    [("round", run_round, run_round.Round), ("series", run_series, run_series.Series)],
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
    module.main()
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


@pytest.mark.parametrize("kind,module", [("round", run_round), ("series", run_series)])
def test_missing_model_fails_before_allocating_any_state(
    repo, monkeypatch, kind, module
):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(plan_for(kind))))
    monkeypatch.setattr(
        sys, "argv", [f"mutagi {kind}", "--repo", str(repo), "start", "--plan", "-"]
    )
    with pytest.raises(ValueError, match="schema"):
        module.main()
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
    with pytest.raises(ValueError, match="rate card model"):
        run_series.main()
    assert not (repo / ".mutagi/runs").exists()


def test_check_resumes_the_owner_of_an_unfinished_child(repo):
    plan = plan_for("series")
    plan["model"] = "chosen-model"
    directory = run_series.create_series(repo, plan)
    state = json.loads((directory / "state.json").read_text())
    round_id = state["rounds"][0]["id"]
    round_directory = run_round.create_round(
        repo,
        run_series.round_plan(plan, plan["rounds"][0]["tasks"]),
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
    monkeypatch.setattr(
        run_experiment.Runner, "advance", lambda self, recovery: self.state
    )
    run_experiment.main()
    saved = list((repo / ".mutagi/runs").glob("*/task.txt"))
    assert len(saved) == 1
    assert saved[0].read_bytes().decode("utf-8") == text


@pytest.mark.parametrize(
    "instruction,exception",
    [
        (["--prompt", " \n "], ValueError),
        (["--task", "missing-task.txt"], FileNotFoundError),
        (["--prompt", "text", "--task", "-"], SystemExit),
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
        run_experiment.main()
    assert not (repo / ".mutagi/runs").exists()


def test_cli_version_and_check_exit_status(repo, diagnostic_codex, monkeypatch):
    version_output = subprocess.check_output(
        [sys.executable, "-m", "mutagi", "--version"], text=True
    )
    assert version_output.strip() == "mutagi " + version("mutagi")
    command = [sys.executable, "-m", "mutagi", "check", "--repo", str(repo)]
    assert subprocess.run(command, capture_output=True).returncode == 0
    monkeypatch.setenv("TEST_AUTH_STATUS", "1")
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 1
    assert "BLOCKED Authentication" in result.stdout
    assert "private-auth-output" not in result.stdout + result.stderr


def test_sample_can_be_copied_tested_and_prepared(tmp_path):
    root = tmp_path / "copied-example"
    source = Path(__file__).resolve().parents[1] / "examples/minimal-python"
    shutil.copytree(source, root)
    subprocess.run(
        ["git", "init", "-b", "main", str(root)], check=True, capture_output=True
    )
    git(root, "config", "user.name", "Test")
    git(root, "config", "user.email", "test@example.invalid")
    subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    commit(root)
    for kind, module in (("round", run_round), ("series", run_series)):
        parser = argparse.ArgumentParser()
        add_plan_arguments(parser, series=kind == "series")
        args = parser.parse_args(
            [
                "--plan",
                str(root / f".mutagi/plans/{kind}.json"),
                "--model",
                "chosen-model",
            ]
        )
        module.validate_plan(read_plan(args, module.validate_plan))
    with run_experiment.lock(root):
        directory = run_experiment.create(
            root,
            "HEAD",
            (root / ".mutagi/plans/first.txt").read_text(),
            "chosen-model",
            "medium",
            60,
            10,
        )
    job = run_experiment.Runner(directory)
    job.prepare()
    assert job.state["phase"] == "ready"
    assert record_template() in job.prompt()
