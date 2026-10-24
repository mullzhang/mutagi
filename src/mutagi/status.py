"""Present saved checkpoints and current process observations without changing them."""

from datetime import datetime, timezone
import json
import shlex
import subprocess
import time

from mutagi.runner_process import boot_identity, processes


def add_status_arguments(parser, validate_id):
    parser.add_argument("id", type=validate_id)
    parser.add_argument(
        "--json", action="store_true", help="Print the original saved state JSON"
    )


def state_path(repo, kind, identifier):
    from mutagi.run_experiment import validate_id

    directory = repo / ".mutagi/runs"
    if kind != "experiment":
        directory /= {"round": "rounds", "series": "series"}[kind]
    return directory / validate_id(identifier) / "state.json"


def load_state(path):
    try:
        state = json.loads(path.read_text())
    except (OSError, ValueError) as error:
        raise ValueError(f"{path}: cannot read saved state: {error}") from error
    if not isinstance(state, dict):
        raise ValueError(f"{path}: expected a saved state object")
    return state


def observed_processes(state):
    owned = state.get("process")
    if owned is None:
        return "No managed process is recorded."
    try:
        if owned["boot_identity"] != boot_identity():
            return "Recorded processes belong to an earlier system boot."
        live = processes()
    except (OSError, subprocess.SubprocessError) as error:
        return f"Process observation unavailable: {error}"
    known = [owned, *owned["children"]]
    matched = [
        str(item["pid"])
        for item in known
        if item["pid"] in live and live[item["pid"]]["identity"] == item["identity"]
    ]
    if matched:
        return "Recorded managed processes are present: " + ", ".join(matched)
    return "No recorded managed process is currently present."


def deadline_line(state):
    deadline = state["deadline"]
    instant = datetime.fromtimestamp(deadline, timezone.utc).isoformat(
        timespec="seconds"
    )
    remaining = max(0, deadline - time.time())
    return f"Deadline: {instant}; {remaining:.0f}s until deadline (not a completion estimate)"


def execution_summary(repo, kind, state):
    lines = [
        f"{kind.capitalize()}: {state['id']}",
        f"Recorded phase: {state['phase']}",
        deadline_line(state),
    ]
    settings = state if kind == "experiment" else state["plan"]
    lines.append(f"Model: {settings['model']}; reasoning effort: {settings['effort']}")
    if state.get("reason") is not None:
        lines.append("Recorded exit reason: " + state["reason"])
    if state.get("error"):
        lines.append("Last recorded error: " + state["error"])
    if kind == "experiment":
        lines.append("Observed now: " + observed_processes(state))
        if state["phase"] == "preparing":
            lines.append(
                f"Preparation checkpoint: {state['prepared']}/{len(state['project']['preparation'])} commands completed"
            )
        phases = (
            "preparing",
            "ready",
            "executing",
            "collecting",
            "saving",
            "saved",
            "registered",
        )
        if state["phase"] in phases:
            pending = phases[phases.index(state["phase"]) :]
            lines.append("Stages from this checkpoint: " + " -> ".join(pending))
        elif state["phase"] == "awaiting_app":
            lines.append(
                "Waiting for controller-confirmed App completion before collection and preservation."
            )
        if state.get("app_observation"):
            observed = state["app_observation"]
            lines.append(
                f"Recorded App observation: {observed['turn_status']} at {observed['observed_at']}; not a live App query"
            )
        if state.get("result") is not None:
            result = state["result"]
            if "status" in result:
                lines.append("Reported observation: " + result["status"])
            lines.append("Reported summary: " + result["summary"])
            lines.append("Reported limitations: " + result["limits"])
            if "record" in result:
                if state.get("saved_commit"):
                    lines.append(
                        "Read saved record: "
                        + shlex.join(
                            [
                                "git",
                                "-C",
                                str(repo),
                                "show",
                                state["saved_commit"] + ":" + result["record"],
                            ]
                        )
                    )
                else:
                    lines.append(
                        "Proposed record (not preserved yet): " + result["record"]
                    )
        if state.get("saved_commit"):
            lines.append("Saved revision: " + state["saved_commit"])
        if state.get("registry_commit"):
            lines.append("Catalog/publication revision: " + state["registry_commit"])
        if "published" in state:
            lines.append(
                "Knowledge publication: "
                + ("published" if state["published"] else "deferred")
            )
    else:
        key, child_kind = (
            ("jobs", "experiment") if kind == "round" else ("rounds", "round")
        )
        items = state[key]
        lines.append(
            f"Recorded progress: {state['next']}/{len(items)} {key}; {len(items) - state['next']} remaining at this checkpoint"
        )
        for index, item in enumerate(items):
            path = state_path(repo, child_kind, item["id"])
            if path.is_file():
                child = load_state(path)
                detail = child["phase"]
                if child.get("saved_commit"):
                    detail += "; saved " + child["saved_commit"]
                if child.get("integration"):
                    detail += "; knowledge " + child["integration"]
                lines.append(
                    f"  {index}: {item.get('kind', child_kind)} {item['id']}: {detail}"
                )
            else:
                detail = (
                    "not allocated"
                    if item["allocation"] is None
                    else "allocation recorded; child checkpoint missing"
                )
                lines.append(f"  {index}: {item['id']}: {detail}")
        if state["next"] < len(items):
            current = state_path(repo, child_kind, items[state["next"]]["id"])
            if current.is_file():
                child = load_state(current)
                lines.append("Current child details:")
                lines.extend(
                    "  " + line for line in execution_summary(repo, child_kind, child)
                )
        if "integration" in state:
            lines.append("Knowledge publication: " + state["integration"])
    return lines


def resume_owner(repo, kind, state):
    if "series_id" in state:
        return "series", state["series_id"]
    if kind == "experiment":
        for path in sorted((repo / ".mutagi/runs/rounds").glob("*/state.json")):
            owner = load_state(path)
            if any(item["id"] == state["id"] for item in owner["jobs"]):
                return (
                    ("series", owner["series_id"])
                    if "series_id" in owner
                    else ("round", owner["id"])
                )
    return kind, state["id"]


def show_execution(repo, kind, identifier, *, raw=False):
    from mutagi.run_round import TERMINAL as ROUND_TERMINAL
    from mutagi.run_series import TERMINAL as SERIES_TERMINAL

    path = state_path(repo, kind, identifier)
    if raw:
        print(path.read_text(), end="")
        return
    state = load_state(path)
    lines = execution_summary(repo, kind, state)
    lines.append("State file: " + str(path))
    if kind == "experiment":
        lines.append("Catalog: " + str(repo / state["project"]["catalog"]))
    lines.append(
        "Human adoption: not tracked by execution; review the catalog and harvesting notes."
    )
    terminal = {
        "experiment": ("registered",),
        "round": ROUND_TERMINAL,
        "series": SERIES_TERMINAL,
    }[kind]
    if state["phase"] in terminal:
        lines.append(
            "Next: inspect saved records and knowledge before choosing results to harvest."
        )
    elif kind == "experiment" and (
        state["phase"] == "awaiting_app" or "app_observation" in state
    ):
        lines.append("Assigned App thread: " + str(state["thread_id"]))
        lines.append(
            "Next: the controller must confirm the assigned App turn and supply its observation to "
            + shlex.join(["mutagi", "handoff", "--run", str(path.parent)])
            + " --app-observation <verified-observation.json>"
        )
    else:
        owner_kind, owner_id = resume_owner(repo, kind, state)
        command = shlex.join(
            ["mutagi", owner_kind, "--repo", str(repo), "resume", owner_id]
        )
        lines.append("Next, if the controller has stopped: " + command)
        lines.append(
            "If a controller or service is still running, follow its status before manual recovery."
        )
    print("\n".join(lines))


def show_service(repo, *, raw=False):
    path = repo / ".mutagi/runs/service.json"
    if raw:
        print(path.read_text() if path.exists() else '{"phase":"unassigned"}', end="\n")
        return
    if not path.exists():
        print(
            "Service: unassigned\nNext: start or manage a series with mutagi service."
        )
        return
    state = load_state(path)
    print(
        f"Service recorded phase: {state['phase']}\nManaged series: {state['id']}\nEnabled: {state['enabled']}\nRecovery attempts: {state['attempts']}\nLast checkpoint: {state['updated_at']}"
    )
    print(
        "Pause requested: "
        + ("yes" if (repo / ".mutagi/runs/service.pause").exists() else "no")
    )
    if state.get("error"):
        print("Last recorded error: " + state["error"])
    print(
        "The service checkpoint does not prove that launchd or the controller is currently running."
    )
    show_execution(repo, "series", state["id"])


def show_job(directory, state):
    lines = [
        "Job: " + state["id"],
        "Recorded stage: " + state["stage"],
        "Command: " + shlex.join(state["command"]),
        "Working directory: " + state["cwd"],
        deadline_line(state),
        "Observed now: " + observed_processes(state),
        "Supervisor observed now: "
        + ("present" if state["supervisor_alive"] else "absent"),
        "Command log: " + str(directory / "command.log"),
        "State file: " + str(directory / "state.json"),
    ]
    if state["result"] is not None:
        lines.append("Recorded result: " + json.dumps(state["result"], sort_keys=True))
    if state.get("error"):
        lines.append("Last recorded error: " + state["error"])
    if state["stage"] in ("finished", "recovered"):
        lines.append("Next: inspect the command log and recorded exit result.")
    elif state["supervisor_alive"]:
        lines.append(
            "Next: wait, or request a stop with "
            + shlex.join(["mutagi", "job", "stop", str(directory)])
        )
    else:
        lines.append(
            "Next: " + shlex.join(["mutagi", "job", "recover", str(directory)])
        )
    print("\n".join(lines))
