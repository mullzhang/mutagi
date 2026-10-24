#!/usr/bin/env python3
"""Recover explicitly managed series after a process restart or login."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import signal
import subprocess
import sys
import time

from mutagi.run_experiment import atomic_json, git, lock, utc, validate_id
from mutagi.run_series import Series, TERMINAL, create_series, validate_plan
from mutagi.inputs import add_plan_arguments, read_plan
from mutagi.status import show_service


class HostShutdown(BaseException):
    """Distinguish interruption from a persistent operator pause request."""


def config_path(repo):
    return repo / ".mutagi/runs/service.json"


def manage(repo, identifier):
    directory = repo / ".mutagi/runs/series" / validate_id(identifier)
    state = json.loads((directory / "state.json").read_text())
    if state["repo"] != str(repo) or state["phase"] in TERMINAL:
        raise ValueError("only an unfinished series in this repository can be managed")

    path = config_path(repo)
    if path.exists():
        prior = json.loads(path.read_text())
        if prior["id"] != identifier:
            previous = Series(repo / ".mutagi/runs/series" / prior["id"])
            if previous.state["phase"] not in TERMINAL:
                raise RuntimeError("another managed series is unfinished")

    pause = repo / ".mutagi/runs/service.pause"
    if pause.exists():
        pause.unlink()

    if state["phase"] == "stopped":
        state["phase"] = "running"
        atomic_json(directory / "state.json", state)

    atomic_json(
        path,
        {
            "id": identifier,
            "enabled": True,
            "attempts": 0,
            "phase": "pending",
            "updated_at": utc(time.time()),
        },
    )


def tick(repo):
    path = config_path(repo)
    if not path.exists():
        return

    with lock(repo):
        config = json.loads(path.read_text())
        if not config["enabled"]:
            return

        controller = Series(repo / ".mutagi/runs/series" / validate_id(config["id"]))
        if (
            controller.state["phase"] in TERMINAL
            or controller.state["phase"] == "stopped"
        ):
            config.update(
                enabled=False,
                phase=controller.state["phase"],
                updated_at=utc(time.time()),
            )
            atomic_json(path, config)
            return

        # Retry service failures while retaining the original ID, model deadline,
        # and preservation checkpoint, up to three attempts. Never restart the model.
        if config["attempts"] >= 3:
            config.update(
                enabled=False, phase="needs_attention", updated_at=utc(time.time())
            )
            atomic_json(path, config)
            return

        config.update(
            attempts=config["attempts"] + 1,
            phase="running",
            updated_at=utc(time.time()),
        )
        atomic_json(path, config)
        try:
            result = controller.advance(recovery=True)
            config.update(
                enabled=False, phase=result["phase"], updated_at=utc(time.time())
            )
        except HostShutdown:
            config.update(phase="recovery_pending", updated_at=utc(time.time()))
            atomic_json(path, config)
            raise
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
            config.update(
                phase="recovery_pending", error=str(error), updated_at=utc(time.time())
            )
        finally:
            atomic_json(path, config)


def installation(repo):
    suffix = hashlib.sha256(str(repo).encode()).hexdigest()[:12]
    label = "org.mutagi." + suffix
    path = Path.home() / "Library/LaunchAgents" / (label + ".plist")
    return label, path


def install(repo):
    if sys.platform != "darwin":
        raise RuntimeError("this supervisor requires launchd on macOS")

    label, path = installation(repo)
    log = repo / ".mutagi/runs/service.log"
    log.parent.mkdir(exist_ok=True)
    # Save the executable search path needed after login. The default launchd
    # PATH does not include Homebrew or user-installed uv/apm.
    folders = [
        str(Path(shutil.which(name)).parent)
        for name in ("codex", "uv", "apm", "git")
        if shutil.which(name)
    ]
    folders.extend(["/usr/bin", "/bin", "/usr/sbin", "/sbin"])
    value = {
        "Label": label,
        "ProgramArguments": [
            sys.executable,
            "-m",
            "mutagi.series_service",
            "--repo",
            str(repo),
            "tick",
        ],
        "WorkingDirectory": str(repo),
        "RunAtLoad": True,
        "StartInterval": 30,
        "EnvironmentVariables": {"PATH": ":".join(dict.fromkeys(folders))},
        "StandardOutPath": str(log),
        "StandardErrorPath": str(log),
        "ProcessType": "Background",
    }
    content = plistlib.dumps(value)
    if path.exists() and path.read_bytes() != content:
        raise RuntimeError(
            "the existing LaunchAgent differs; explicitly uninstall it before replacement"
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    domain = f"gui/{os.getuid()}"
    existing = subprocess.run(
        ["launchctl", "print", domain + "/" + label], capture_output=True
    )
    if existing.returncode:
        subprocess.run(["launchctl", "bootstrap", domain, str(path)], check=True)

    subprocess.run(
        ["launchctl", "print", domain + "/" + label],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    atomic_json(
        repo / ".mutagi/runs/service-installation.json",
        {
            "label": label,
            "path": str(path),
            "sha256": hashlib.sha256(content).hexdigest(),
            "installed_at": utc(time.time()),
        },
    )
    print(json.dumps({"label": label, "path": str(path)}))


def main():
    parser = argparse.ArgumentParser(prog="mutagi service", description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("install")
    commands.add_parser("uninstall")
    commands.add_parser("tick")
    commands.add_parser("status").add_argument(
        "--json", action="store_true", help="Print the saved service state JSON"
    )
    add_plan_arguments(commands.add_parser("start"), series=True)
    commands.add_parser("manage").add_argument("id", type=validate_id)
    commands.add_parser("pause")
    args = parser.parse_args()
    repo = Path(git(args.repo, "rev-parse", "--show-toplevel")).resolve()

    def shutdown(*_):
        raise HostShutdown()

    signal.signal(signal.SIGTERM, shutdown)
    if args.command == "install":
        install(repo)
    elif args.command == "tick":
        tick(repo)
    elif args.command == "status":
        show_service(repo, raw=args.json)
    elif args.command == "pause":
        # Write the request atomically even while the controller holds the lock.
        (repo / ".mutagi/runs/service.pause").write_text("operator requested a pause\n")
        print(
            "Pause requested. The active task will be preserved before any further work starts."
        )
    elif args.command == "uninstall":
        label, path = installation(repo)
        subprocess.run(
            ["launchctl", "bootout", f"gui/{os.getuid()}/" + label], check=True
        )
        path.unlink()
    else:
        if args.command == "start":
            plan = read_plan(args, validate_plan, repo=repo)
            if args.dry_run:
                print(json.dumps(plan, ensure_ascii=False, indent=2))
                return
        with lock(repo):
            identifier = (
                create_series(repo, plan).name if args.command == "start" else args.id
            )
            manage(repo, identifier)
            print(identifier)


if __name__ == "__main__":
    try:
        main()
    except HostShutdown:
        raise SystemExit(0) from None
