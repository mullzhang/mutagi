"""Inspect execution prerequisites without starting work or changing the project."""

import argparse
from importlib.metadata import version
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

from mutagi.repository import (
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
    if os.name != "posix":
        raise ValueError("execution-state inspection requires a supported POSIX system")
    from mutagi.run_experiment import validate_id
    from mutagi.run_round import TERMINAL as ROUND_TERMINAL
    from mutagi.run_series import TERMINAL as SERIES_TERMINAL

    groups = {}
    for kind, pattern in (
        ("series", "series/*/state.json"),
        ("round", "rounds/*/state.json"),
        ("experiment", "*/state.json"),
    ):
        groups[kind] = []
        for path in sorted((repo / ".mutagi/runs").glob(pattern)):
            try:
                state = json.loads(path.read_text())
                if not isinstance(state, dict) or not {"id", "phase"} <= state.keys():
                    raise ValueError("expected an execution ID and phase")
                if not isinstance(state["id"], str) or not isinstance(
                    state["phase"], str
                ):
                    raise ValueError("execution ID and phase must be strings")
                validate_id(state["id"])
                if "series_id" in state:
                    if not isinstance(state["series_id"], str):
                        raise ValueError("series_id must be a UUID string")
                    validate_id(state["series_id"])
                if kind == "round" and (
                    not isinstance(state.get("jobs"), list)
                    or any(
                        not isinstance(job, dict)
                        or "id" not in job
                        or not isinstance(job["id"], str)
                        for job in state["jobs"]
                    )
                ):
                    raise ValueError("expected the round's allocated jobs")
            except (ValueError, OSError) as error:
                raise ValueError(
                    f"{path}: cannot inspect execution state: {error}"
                ) from error
            groups[kind].append(state)
    owners = {job["id"]: state for state in groups["round"] for job in state["jobs"]}
    commands = []
    for kind, terminal in (
        ("series", SERIES_TERMINAL),
        ("round", ROUND_TERMINAL),
        ("experiment", ("registered",)),
    ):
        for state in groups[kind]:
            if state["phase"] in terminal:
                continue
            identifier = state["id"]
            owner_kind = kind
            if "series_id" in state:
                owner_kind, identifier = "series", state["series_id"]
            elif kind == "experiment" and identifier in owners:
                owner = owners[identifier]
                owner_kind, identifier = (
                    ("series", owner["series_id"])
                    if "series_id" in owner
                    else ("round", owner["id"])
                )
            commands.append(
                shlex.join(
                    ["mutagi", owner_kind, "--repo", str(repo), "resume", identifier]
                )
            )
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
