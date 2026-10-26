"""Present saved checkpoints and current process observations without changing them."""

from __future__ import annotations

import json
import shlex
import subprocess
import time
from datetime import datetime, timezone

from mutagi.execution.state import (
    EXPERIMENT_TERMINAL,
    ROUND_TERMINAL,
    SERIES_TERMINAL,
    load_state,
    resume_owner,
    state_path,
)
from mutagi.runtime.process import boot_identity, processes
from mutagi.commands import display
from mutagi.commands.common import invocation


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


def show_execution(repo, kind, identifier, *, raw=False, details=False):
    path = state_path(repo, kind, identifier)
    if raw:
        display.text_payload(path.read_text())
        return
    state = load_state(path)
    lines = execution_summary(repo, kind, state)
    display.title(f"{kind.capitalize()} · {state['phase']} · {identifier[:8]}")
    primary = (
        "Recorded phase:",
        "Recorded progress:",
        "Observed now:",
        "Reported observation:",
        "Saved revision:",
        "Knowledge publication:",
        "Last recorded error:",
        "Recorded exit reason:",
    )
    display.fields([line.split(": ", 1) for line in lines if line.startswith(primary)])
    if "Current child details:" in lines:
        current = lines[lines.index("Current child details:") + 1 :]
        display.title("Current child")
        display.fields(
            [
                line.strip().split(": ", 1)
                for line in current
                if line.startswith(tuple("  " + label for label in primary))
            ]
        )
    for label in ("Reported summary:", "Reported limitations:"):
        for line in lines:
            if line.startswith(label):
                display.fields(
                    [
                        (
                            label[:-1] + (" excerpt" if not details else ""),
                            line.split(": ", 1)[1]
                            if details
                            else display.excerpt(line.split(": ", 1)[1]),
                        )
                    ]
                )
    display.note(
        "Human adoption: not tracked by execution. Preservation does not establish independent reproduction or adoption."
    )
    terminal = {
        "experiment": EXPERIMENT_TERMINAL,
        "round": ROUND_TERMINAL,
        "series": SERIES_TERMINAL,
    }[kind]
    if state["phase"] in terminal:
        display.next_steps(invocation(repo, "harvest", "list"))
    elif kind == "experiment" and (
        state["phase"] == "awaiting_app" or "app_observation" in state
    ):
        if state.get("thread_id"):
            display.fields([("Assigned App thread", state["thread_id"])])
        display.note(
            "Waiting for controller-confirmed App completion; this is not a live App query."
        )
        display.next_steps(
            shlex.join(["mutagi", "handoff", "--run", str(path.parent)])
            + " --app-observation <verified-observation.json>"
        )
    else:
        owner_kind, owner_id = resume_owner(repo, kind, state)
        display.note(
            "If the controller or service is still running, follow its status. Resume only after it has stopped."
        )
        display.next_steps(invocation(repo, owner_kind, "resume", owner_id))
    if details:
        display.title("Supporting checkpoints and references")
        for line in lines:
            display.console().print(line)
        display.fields([("State file", path)])
    else:
        display.note(
            "More: --details for checkpoints and references; --json for the saved state."
        )


def show_service(repo, *, raw=False, details=False):
    path = repo / ".mutagi/runs/service.json"
    if raw:
        display.text_payload(
            path.read_text() if path.exists() else '{"phase":"unassigned"}\n'
        )
        return
    if not path.exists():
        display.title("Service · unassigned")
        display.next_steps(
            invocation(repo, "service", "start") + " --help",
            invocation(repo, "service", "manage") + " <series-UUID>",
        )
        return
    state = load_state(path)
    display.title("Service · " + state["phase"])
    display.fields(
        [
            ("Managed series", state["id"] if details else state["id"][:8]),
            ("Enabled", state["enabled"]),
            ("Recovery attempts", state["attempts"]),
            ("Pause requested", (repo / ".mutagi/runs/service.pause").exists()),
        ]
    )
    if details:
        display.fields([("Last checkpoint", state["updated_at"])])
    if state.get("error"):
        display.note("Last recorded error: " + state["error"])
    display.note("This checkpoint does not prove launchd or its controller is running.")
    show_execution(repo, "series", state["id"], details=details)


def show_job(directory, state, *, details=False):
    display.title("Job · " + state["stage"] + " · " + state["id"][:8])
    items = [
        ("Recorded stage", state["stage"]),
        ("Observed now", observed_processes(state)),
        (
            "Supervisor observed now",
            "present" if state["supervisor_alive"] else "absent",
        ),
    ]
    if state["result"] is not None:
        items.append(
            ("Recorded exit result", json.dumps(state["result"], sort_keys=True))
        )
    if state.get("error"):
        items.append(("Last recorded error", state["error"]))
    display.fields(items)
    if state["stage"] in ("finished", "recovered"):
        display.next_steps("Inspect " + str(directory / "command.log"))
    elif state["supervisor_alive"]:
        display.next_steps(
            shlex.join(["mutagi", "job", "status", str(directory)]),
            shlex.join(["mutagi", "job", "stop", str(directory)]),
        )
    else:
        display.next_steps(shlex.join(["mutagi", "job", "recover", str(directory)]))
    if details:
        display.fields(
            [
                ("Command", shlex.join(state["command"])),
                ("Working directory", state["cwd"]),
                ("Deadline", deadline_line(state)),
                ("Command log", directory / "command.log"),
                ("State file", directory / "state.json"),
            ]
        )
    else:
        display.note(
            "More: --details for command, deadline and log paths; --json for the saved state."
        )
