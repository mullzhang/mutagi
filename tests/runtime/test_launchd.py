"""Verify saved service commands without modifying the user's LaunchAgents."""

import hashlib
import json
import plistlib
import subprocess
import sys
from pathlib import Path

import pytest

from mutagi.runtime import launchd


@pytest.fixture
def launchd_environment(repo, tmp_path, monkeypatch):
    home = tmp_path / "home"
    calls = []
    loaded = set()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(launchd.shutil, "which", lambda name: "/test/bin/" + name)

    def run(command, **kwargs):
        calls.append(command)
        action = command[1]
        if action == "bootstrap":
            label = plistlib.loads(Path(command[3]).read_bytes())["Label"]
            loaded.add(command[2] + "/" + label)
        elif action == "bootout":
            loaded.remove(command[2])
        code = int(action == "print" and command[2] not in loaded)
        if code and kwargs.get("check"):
            raise subprocess.CalledProcessError(code, command)
        return subprocess.CompletedProcess(command, code)

    monkeypatch.setattr(launchd.subprocess, "run", run)
    return repo, home, calls


def test_install_records_public_service_command_and_is_repeatable(launchd_environment):
    repo, home, calls = launchd_environment
    result = launchd.install(repo)
    path = Path(result["path"])
    assert path.parent == home / "Library/LaunchAgents"
    contents = path.read_bytes()
    agent = plistlib.loads(contents)
    assert agent["ProgramArguments"] == [
        sys.executable,
        "-m",
        "mutagi",
        "service",
        "--repo",
        str(repo),
        "tick",
    ]
    assert agent["WorkingDirectory"] == str(repo)
    assert agent["RunAtLoad"] and agent["StartInterval"] == 30
    assert (
        agent["EnvironmentVariables"]["PATH"]
        == "/test/bin:/usr/bin:/bin:/usr/sbin:/sbin"
    )
    receipt = json.loads((repo / ".mutagi/runs/service-installation.json").read_text())
    assert receipt["label"] == result["label"]
    assert receipt["path"] == str(path)
    assert receipt["sha256"] == hashlib.sha256(contents).hexdigest()
    assert launchd.install(repo) == result
    assert path.read_bytes() == contents
    assert sum(command[1] == "bootstrap" for command in calls) == 1


def test_install_rejects_different_saved_command_before_replacement(
    launchd_environment,
):
    repo, _, calls = launchd_environment
    _, path = launchd.installation(repo)
    path.parent.mkdir(parents=True)
    previous = plistlib.dumps(
        {"ProgramArguments": ["python", "-m", "mutagi.series_service"]}
    )
    path.write_bytes(previous)
    with pytest.raises(RuntimeError, match="explicitly uninstall"):
        launchd.install(repo)
    assert path.read_bytes() == previous
    assert not calls
    assert not (repo / ".mutagi/runs/service-installation.json").exists()


def test_uninstall_removes_only_the_registered_agent(launchd_environment):
    repo, home, calls = launchd_environment
    installed = launchd.install(repo)
    unrelated = home / "Library/LaunchAgents/unrelated.plist"
    unrelated.write_bytes(b"unrelated agent")
    checkpoint = repo / ".mutagi/runs/service.json"
    checkpoint.write_text('{"id":"saved-series","enabled":false}\n')
    launchd.uninstall(repo)
    assert calls[-1][1] == "bootout"
    assert calls[-1][2].endswith("/" + installed["label"])
    assert not Path(installed["path"]).exists()
    assert unrelated.read_bytes() == b"unrelated agent"
    assert checkpoint.read_text() == '{"id":"saved-series","enabled":false}\n'
