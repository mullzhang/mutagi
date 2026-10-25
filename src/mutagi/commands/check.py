"""Inspect execution prerequisites without starting work or changing the project."""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path

from mutagi.execution.state import unfinished_executions
from mutagi.project.repository import (
    project_at,
    repository_root,
    require_branch,
    require_clean,
    require_identity,
    require_private_paths,
    resolve_base,
)


def command_output(command):
    result = subprocess.run(command, capture_output=True, text=True, timeout=15)
    if result.returncode:
        raise ValueError(
            shlex.join(command) + " failed; install/configure the command and retry"
        )
    return result.stdout.strip()


def check_runtime():
    if os.name != "posix" or sys.version_info < (3, 12):
        raise ValueError("use Python 3.12 or later on a POSIX system")
    return (
        f"mutagi {version('mutagi')}; Python {sys.version.split()[0]}; {sys.platform}"
    )


def check_codex_options():
    output = command_output(["codex", "exec", "--help"])
    required = (
        "--json",
        "--approve-for-me",
        "--model",
        "--config",
        "--output-schema",
        "--output-last-message",
    )
    missing = [option for option in required if option not in output]
    if missing:
        raise ValueError("install a Codex CLI supporting: " + ", ".join(missing))
    return "required execution options are available"


def check_authentication():
    try:
        command_output(["codex", "login", "status"])
    except ValueError as error:
        raise ValueError(
            "Codex is not authenticated; run codex login and retry"
        ) from error
    return "Codex login status succeeded; model access has not been tested"


def unfinished_work(repo):
    commands = [
        shlex.join(["mutagi", kind, "--repo", str(repo), "resume", identifier])
        for item in unfinished_executions(repo)
        for kind, identifier in [item["owner"]]
    ]
    if commands:
        raise ValueError(
            "resume unfinished work:\n" + "\n".join(dict.fromkeys(commands))
        )
    return "no unfinished executions"


def inspect(repo, base):
    results = []

    def collect(name, function):
        try:
            detail = function()
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            results.append((name, False, str(error)))
            return None
        results.append(
            (name, True, str(detail) if isinstance(detail, (str, Path)) else "passed")
        )
        return detail

    collect("Runtime", check_runtime)
    collect("Git", lambda: command_output(["git", "--version"]))
    collect(
        "Process inspection",
        lambda: command_output(["ps", "-p", str(os.getpid()), "-o", "lstart="]),
    )
    collect("Codex", lambda: command_output(["codex", "--version"]))
    collect("Codex options", check_codex_options)
    collect("Authentication", check_authentication)
    root = collect("Repository", lambda: repository_root(repo))
    if root is None:
        return results
    collect("Branch", lambda: require_branch(root))
    revision = collect("Base commit", lambda: resolve_base(root, base))
    collect("Working tree", lambda: require_clean(root))
    collect("Git identity", lambda: require_identity(root))
    collect("Private storage", lambda: require_private_paths(root))
    collect("Working project files", lambda: project_at(root))
    if revision is not None:
        project = collect("Committed project files", lambda: project_at(root, revision))
        if project is not None:
            commands = "; ".join(
                shlex.join(command) for command in project["preparation"]
            )
            results.append(
                (
                    "Preparation",
                    True,
                    "configured but not executed: " + commands
                    if commands
                    else "no preparation commands configured",
                )
            )
    collect("Existing executions", lambda: unfinished_work(root))
    return results


def main():
    parser = argparse.ArgumentParser(prog="mutagi check", description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument(
        "--base", default="HEAD", help="Committed project revision (default: HEAD)"
    )
    args = parser.parse_args()
    results = inspect(args.repo, args.base)
    for name, passed, detail in results:
        print(f"{'OK' if passed else 'BLOCKED'} {name}: {detail}")
    print(
        "This check does not run preparation, test model access, write Git state, or verify future network access."
    )
    if not all(passed for _, passed, _ in results):
        raise SystemExit(1)
