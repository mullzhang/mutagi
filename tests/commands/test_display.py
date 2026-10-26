"""Public inputs, terminal presentation, and payload stream contracts."""

import io
import json
import signal
import subprocess

import pytest
from rich.cells import cell_len
from rich.console import Console
from typer.testing import CliRunner

from mutagi.cli import app
from mutagi.commands import check, display, harvest, job, service
from mutagi.execution.experiment import Runner
from mutagi.execution.round import Round
from mutagi.execution.series import Series
from mutagi.execution.catalog import read_experiment
from mutagi.execution.state import load_state
from mutagi.prompts import record_template
from mutagi.project.repository import git
from tests.support.plans import plan_args
from tests.support.repositories import snapshot


GROUPS = {
    "experiment": ("start", "resume", "status"),
    "round": ("start", "resume", "status"),
    "series": ("start", "resume", "status"),
    "service": ("install", "uninstall", "start", "manage", "pause", "status", "tick"),
    "job": ("start", "status", "stop", "recover"),
    "harvest": ("list", "show", "checkout", "prepare", "record"),
    "template": ("experiment",),
}


def test_every_public_action_has_help_outside_a_repository(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    root = runner.invoke(app, ["--help"])
    assert root.exit_code == 0, root.output
    assert "reuse saved results" in root.stdout
    for action in ("init", "check", "handoff"):
        result = runner.invoke(app, [action, "--help"])
        assert result.exit_code == 0 and "Usage:" in result.stdout
    for group, actions in GROUPS.items():
        result = runner.invoke(app, [group, "--help"])
        assert result.exit_code == 0, result.output
        group_help = result.stdout
        for action in actions:
            assert action in group_help
            result = runner.invoke(app, [group, action, "--help"])
            assert result.exit_code == 0, result.output
            assert "Usage:" in result.stdout and not result.stderr
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "arguments",
    [
        ["missing-command"],
        ["experiment", "missing-action"],
        ["experiment", "start"],
        ["experiment", "resume", "bad-id"],
        ["round", "status", "12345678"],
        ["series", "resume", "BAD-ID"],
        ["service", "manage", "bad-id"],
        ["init", "--preset", "guessed"],
        ["job", "start"],
        ["handoff"],
        ["harvest", "show", "1234567"],
        ["harvest", "prepare", "12345678", "--kind", "guessed"],
    ],
)
def test_typed_usage_errors_use_stderr_and_exit_two(arguments, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(app, arguments)
    assert result.exit_code == 2, result.output
    assert not result.stdout and "Error" in result.stderr
    assert "Traceback" not in result.stderr
    assert list(tmp_path.iterdir()) == []


def test_operational_errors_use_stderr_and_exit_one(repo):
    result = CliRunner().invoke(
        app,
        [
            "round",
            "--repo",
            str(repo),
            "status",
            "11111111-1111-4111-8111-111111111111",
        ],
    )
    assert result.exit_code == 1 and not result.stdout
    assert "Error" in result.stderr and "Traceback" not in result.stderr


def test_next_commands_keep_the_selected_repository_and_base(
    repo, empty_repo, tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        check, "inspect", lambda *_: [("Authentication", False, "Log in")]
    )
    result = CliRunner().invoke(
        app, ["check", "--repo", str(repo), "--base", "chosen-base"]
    )
    assert result.exit_code == 1 and not result.stderr
    assert f"mutagi check --repo {repo} --base chosen-base" in result.stdout
    result = CliRunner().invoke(app, ["init", "--repo", str(empty_repo)])
    assert result.exit_code == 0, result.output
    assert f"cd {empty_repo}" in result.stdout
    monkeypatch.setattr(
        service, "install_agent", lambda _: {"label": "isolated fixture"}
    )
    result = CliRunner().invoke(app, ["service", "--repo", str(repo), "install"])
    assert result.exit_code == 0, result.output
    assert f"mutagi service --repo {repo} status" in result.stdout


def test_blocked_check_json_is_one_payload_with_a_failure_exit(repo, monkeypatch):
    monkeypatch.setattr(
        check, "inspect", lambda *_: [("Authentication", False, "Log in")]
    )
    result = CliRunner().invoke(
        app, ["check", "--repo", str(repo), "--json"], env={"FORCE_COLOR": "1"}
    )
    assert result.exit_code == 1 and not result.stderr
    assert json.loads(result.stdout) == {
        "ready": False,
        "checks": [{"name": "Authentication", "passed": False, "detail": "Log in"}],
    }


@pytest.mark.parametrize("width", [40, 60, 80, 120])
def test_rich_lists_wrap_literal_source_text_at_terminal_width(monkeypatch, width):
    stream = io.StringIO()
    monkeypatch.setattr(
        display,
        "console",
        lambda **kwargs: Console(
            file=stream, width=width, color_system=None, markup=False, highlight=False
        ),
    )
    display.title("Results · 日本語")
    display.table(
        ["Ref", "Question", "Observation"],
        [
            ("12345678", "[red] literal 日本語 " + "long " * 15, "refuted"),
        ],
    )
    output = stream.getvalue()
    assert "[red]" in output and "日本語" in output and "refuted" in output
    assert "\x1b" not in output
    assert all(cell_len(line) <= width for line in output.splitlines())
    if width <= 60:
        assert output.splitlines()[1] == "12345678"


@pytest.mark.parametrize(
    "environment",
    [{"FORCE_COLOR": "1", "COLUMNS": "40"}, {"NO_COLOR": "1", "COLUMNS": "60"}],
)
def test_templates_previews_records_and_diffs_are_undecorated(
    repo, preserved_experiment, environment
):
    runner = CliRunner()
    template = runner.invoke(app, ["template", "experiment"], env=environment)
    assert template.exit_code == 0 and template.stdout == record_template()
    preview = runner.invoke(
        app,
        [
            "round",
            "--repo",
            str(repo),
            "start",
            *plan_args("round", "--step", "none: [red] 日本語: keep this"),
            "--dry-run",
        ],
        env=environment,
    )
    assert preview.exit_code == 0, preview.output
    assert json.loads(preview.stdout)["tasks"] == [
        {"prompt": "[red] 日本語: keep this", "parent": None}
    ]
    assert "\x1b" not in preview.stdout and not preview.stderr
    source = read_experiment(repo, preserved_experiment["id"])
    before = snapshot(repo)
    for mode in ("--record", "--diff"):
        result = runner.invoke(
            app,
            [
                "harvest",
                "--repo",
                str(repo),
                "show",
                preserved_experiment["id"][:8],
                mode,
            ],
            env={
                **environment,
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "color.ui",
                "GIT_CONFIG_VALUE_0": "always",
            },
        )
        assert result.exit_code == 0, result.output
        expected = (
            source["record_text"]
            if mode == "--record"
            else subprocess.check_output(
                [
                    "git",
                    "-C",
                    str(repo),
                    "-c",
                    "color.ui=always",
                    "diff",
                    "--no-ext-diff",
                    "--no-color",
                    source["base_commit"],
                    source["saved_commit"],
                    "--",
                ],
                text=True,
            )
        )
        assert result.stdout == expected and "\x1b" not in result.stdout
    arguments = [
        "harvest",
        "--repo",
        str(repo),
        "prepare",
        source["id"][:8],
        "--purpose",
        "[red] 日本語: inspect evidence",
    ]
    prompt = runner.invoke(app, arguments, env=environment)
    plain = runner.invoke(app, arguments)
    assert prompt.exit_code == plain.exit_code == 0, prompt.output
    assert prompt.stdout == plain.stdout and "\x1b" not in prompt.stdout
    assert prompt.stdout.startswith("Use a preserved exploration result")
    assert snapshot(repo) == before


@pytest.mark.parametrize(
    "kind,controller", [("experiment", Runner), ("round", Round), ("series", Series)]
)
def test_execution_json_keeps_startup_on_stderr(repo, monkeypatch, kind, controller):
    monkeypatch.setattr(controller, "advance", lambda self, recovery: self.state)
    monkeypatch.setattr(signal, "signal", lambda *_: None)
    flags = (
        [
            "--base",
            "HEAD",
            "--prompt",
            "finite case",
            "--model",
            "test-model",
            "--effort",
            "medium",
        ]
        if kind == "experiment"
        else plan_args(kind, "--step", "none: finite case")
    )
    if kind == "series":
        flags += ["--rounds", "2"]
    result = CliRunner().invoke(
        app,
        [kind, "--repo", str(repo), "start", *flags, "--json"],
        env={"FORCE_COLOR": "1"},
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert "\x1b" not in result.stdout
    assert payload["id"] in result.stderr and "started" in result.stderr
    assert payload["phase"] in ("preparing", "running")
    assert payload.get("saved_commit") is None


def test_failed_execution_keeps_recovery_and_saved_error_out_of_json(repo, monkeypatch):
    def fail(self, *, recovery):
        raise RuntimeError("finite test interruption")

    monkeypatch.setattr(Runner, "advance", fail)
    monkeypatch.setattr(signal, "signal", lambda *_: None)
    result = CliRunner().invoke(
        app,
        [
            "experiment",
            "--repo",
            str(repo),
            "start",
            "--base",
            "HEAD",
            "--prompt",
            "case",
            "--model",
            "test",
            "--effort",
            "medium",
            "--json",
        ],
    )
    assert result.exit_code == 1 and not result.stdout
    assert "finite test interruption" in result.stderr and "Recovery" in result.stderr
    state = load_state(next((repo / ".mutagi/runs").glob("*/state.json")))
    assert (
        state["error"] == "finite test interruption" and state["phase"] == "preparing"
    )


def test_job_arguments_after_separator_belong_to_external_command(
    tmp_path, monkeypatch
):
    captured = {}

    def launch(directory, cwd, seconds, command):
        captured.update(
            directory=str(directory), cwd=str(cwd), seconds=seconds, command=command
        )
        return captured

    monkeypatch.setattr(job, "start_job", launch)
    result = CliRunner().invoke(
        app,
        [
            "job",
            "start",
            str(tmp_path / "record"),
            "--cwd",
            str(tmp_path),
            "--seconds",
            "10",
            "--json",
            "--",
            "executable",
            "--json",
            "--flag",
            "two words",
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["command"] == [
        "executable",
        "--json",
        "--flag",
        "two words",
    ]


def test_harvest_prefixes_remain_unambiguous_across_search_results(repo, monkeypatch):
    ids = [
        "12345678-1111-4111-8111-111111111111",
        "12345678-2222-4222-8222-222222222222",
    ]
    entries = [{"id": identifier} for identifier in ids]
    refs = harvest.references(entries)
    assert len(set(refs.values())) == 2
    monkeypatch.setattr(harvest, "catalog_entries", lambda repo: entries)
    result = CliRunner().invoke(
        app, ["harvest", "--repo", str(repo), "show", "12345678"]
    )
    assert result.exit_code == 2 and "ambiguous" in result.stderr
    assert not result.stdout


def test_colorless_redirected_and_no_color_human_output(
    repo, preserved_experiment, monkeypatch
):
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    monkeypatch.delenv("CLICOLOR_FORCE", raising=False)
    for env in ({}, {"NO_COLOR": "1", "TERM": "xterm-256color"}):
        result = CliRunner().invoke(
            app, ["harvest", "--repo", str(repo), "list"], env=env
        )
        assert result.exit_code == 0 and "\x1b" not in result.stdout
        assert preserved_experiment["id"][:8] in result.stdout
        assert preserved_experiment["id"] not in result.stdout
    assert git(repo, "status", "--porcelain") == ""
