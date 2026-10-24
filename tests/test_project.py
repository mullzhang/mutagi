"""Test project paths and settings pinned to the base revision."""

from pathlib import Path
import subprocess
import sys

import pytest

from mutagi import run_experiment as runner
from mutagi.project import parse_project
from test_runner import create, fake_codex as fake_codex, repo as repo


def test_uses_project_paths_and_preserves_start_configuration(
    repo, tmp_path, fake_codex
):
    # Given a project with implementation, experiment, and catalog paths different from naedo.
    (repo / "experiments").rename(repo / "trials")
    config_path = repo / ".mutagi/config.toml"
    text = (
        config_path.read_text()
        .replace('"experiments"', '"trials"')
        .replace('"experiments/INDEX.md"', '"trials/INDEX.md"')
        .replace('"naedo/"', '"src/"')
    )
    config_path.write_text(text)
    runner.git(repo, "add", ".")
    runner.git(repo, "commit", "-m", "test: configure different project layout")
    fake_codex.write_text(
        fake_codex.read_text().replace(
            "pathlib.Path('experiments')", "pathlib.Path('trials')"
        )
    )

    # When settings and scope are checked and exploration, preservation, and registration complete.
    job = create(repo, tmp_path)
    assert job.project["experiments"] == "trials"
    assert job.allowed("src/app.py", job.directory())
    assert not job.allowed("naedo/app.py", job.directory())
    job.advance()

    # Then records use only the configured paths and run state retains the base settings.
    assert job.state["phase"] == "registered"
    assert job.state["result"]["record"].startswith("trials/")
    assert job.state["id"] in (repo / "trials/INDEX.md").read_text()
    assert not (repo / "experiments").exists()
    config_path.write_text(text.replace('"src/"', '"elsewhere/"'))
    restored = runner.Runner(job.run)
    assert restored.allowed("src/app.py", restored.directory())
    assert not restored.allowed("elsewhere/app.py", restored.directory())


@pytest.mark.parametrize("path", ["../outside", "/tmp/outside", ".git/config"])
def test_rejects_paths_outside_project(path):
    # Given an explicit experiment path outside the authorized project.
    text = (
        Path(__file__)
        .with_name("project.toml")
        .read_text()
        .replace('experiments = "experiments"', f'experiments = "{path}"')
    )
    # When validated, then reject it before file operations.
    with pytest.raises(ValueError, match="project-relative path"):
        parse_project(text)


def test_cli_forwards_help_and_ships_record_template(tmp_path):
    # Given an installed module invoked outside its checkout.
    for command in ("init", "check", "experiment", "round", "series", "service", "handoff", "job"):
        # When each subcommand is invoked, then display its own help.
        result = subprocess.run(
            [sys.executable, "-m", "mutagi", command, "--help"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=True,
        )
        assert f"mutagi {command}" in result.stdout

    result = subprocess.run(
        [sys.executable, "-m", "mutagi", "template", "experiment"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "human_decision: unreviewed" in result.stdout
