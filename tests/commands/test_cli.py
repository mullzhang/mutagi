from __future__ import annotations

import subprocess
import sys
from importlib.metadata import version


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


def test_cli_forwards_help_and_ships_record_template(tmp_path):
    # Given an installed module invoked outside its checkout.
    for command in (
        "init",
        "check",
        "experiment",
        "round",
        "series",
        "service",
        "handoff",
        "job",
    ):
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


def test_service_tick_exits_normally_after_saving_shutdown_checkpoint(
    repo, monkeypatch
):
    import json

    from mutagi.commands import service as service_command
    from mutagi.execution.series import Series, create_series
    from mutagi.execution.service import HostShutdown, config_path, manage
    from tests.support.plans import plan_for

    directory = create_series(repo, {**plan_for("series"), "model": "test-model"})
    original = (directory / "state.json").read_bytes()
    manage(repo, directory.name)

    def shutdown(self, *, recovery):
        assert recovery
        raise HostShutdown()

    monkeypatch.setattr(Series, "advance", shutdown)
    monkeypatch.setattr(sys, "argv", ["mutagi service", "--repo", str(repo), "tick"])
    service_command.main()
    checkpoint = json.loads(config_path(repo).read_text())
    assert checkpoint["phase"] == "recovery_pending"
    assert checkpoint["attempts"] == 1 and checkpoint["enabled"]
    assert checkpoint["id"] == directory.name
    assert (directory / "state.json").read_bytes() == original
