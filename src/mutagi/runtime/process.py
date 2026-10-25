"""Track POSIX commands and descendants across process groups."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path


def boot_identity():
    if sys.platform == "darwin":
        return subprocess.check_output(
            ["sysctl", "-n", "kern.boottime"], text=True
        ).strip()

    return Path("/proc/sys/kernel/random/boot_id").read_text().strip()


def identity(pid: int) -> str | None:
    result = subprocess.run(
        ["ps", "-p", str(pid), "-o", "lstart="], capture_output=True, text=True
    )
    return " ".join(result.stdout.split()) or None


def processes():
    output = subprocess.check_output(
        ["ps", "-axo", "pid=,ppid=,pgid=,stat=,lstart="], text=True
    )
    rows = {}
    for line in output.splitlines():
        pid, parent, group, status, started = line.split(maxsplit=4)
        if not status.startswith("Z"):
            rows[int(pid)] = {
                "parent": int(parent),
                "group": int(group),
                "identity": " ".join(started.split()),
            }

    return rows


def refresh(owned, save):
    rows = processes()
    known = [owned, *owned["children"]]
    selected = {
        item["pid"]
        for item in known
        if item["pid"] in rows and rows[item["pid"]]["identity"] == item["identity"]
    }
    while True:
        expanded = selected | {
            pid for pid, row in rows.items() if row["parent"] in selected
        }
        if expanded == selected:
            break

        selected = expanded

    children = [
        {"pid": pid, "identity": rows[pid]["identity"]}
        for pid in sorted(selected - {owned["pid"]})
    ]
    if children != owned["children"]:
        owned["children"] = children
        save()

    return rows, selected


def members(group: int) -> list[int]:
    rows = subprocess.check_output(["ps", "-axo", "pid=,pgid=,stat="], text=True)
    return [
        int(pid)
        for pid, pgid, status in (row.split() for row in rows.splitlines())
        if int(pgid) == group and not status.startswith("Z")
    ]


def stop(owned, save, process=None) -> None:
    group = owned["pid"]
    for sig, grace in ((signal.SIGTERM, 2.0), (signal.SIGKILL, 2.0)):
        rows, selected = refresh(owned, save)
        if group in rows and rows[group]["identity"] != owned["identity"]:
            raise RuntimeError(
                "PID identity changed; refusing to operate on an unrelated process"
            )

        # Codex shell tools can create separate sessions even for foreground commands.
        for pid in selected - {group}:
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                pass

        try:
            os.killpg(group, sig)
        except ProcessLookupError:
            pass

        until = time.monotonic() + grace
        while time.monotonic() < until:
            if process is not None:
                process.poll()

            _, selected = refresh(owned, save)
            if not selected and not members(group):
                break

            time.sleep(0.05)

        if not selected and not members(group):
            break

    if process is not None:
        process.wait(timeout=2)

    _, selected = refresh(owned, save)
    if selected or members(group):
        raise RuntimeError(
            f"managed process {group} or its descendants are still running"
        )


def recover_process(state, save) -> None:
    owned = state.get("process")
    if owned is None:
        return

    if "boot_identity" in owned and owned["boot_identity"] != boot_identity():
        state["process"] = None
        save()
        return

    pid = owned["pid"]
    current = identity(pid)
    if current is not None and current != owned["identity"]:
        raise RuntimeError(
            "PID identity changed; refusing to signal an unrelated process"
        )

    recorded = {child["pid"] for child in owned["children"]}
    if current is None and set(members(pid)) - recorded:
        raise RuntimeError(
            f"group {pid} contains orphaned processes; inspect them before recovery"
        )

    stop(owned, save)

    state["process"] = None
    save()


def run_job(
    command,
    cwd: Path,
    log: Path,
    deadline: float,
    state,
    save,
    *,
    stdin=None,
    env=None,
    cancel_requested=None,
):
    """Persist ownership before execution and while tracking descendants."""
    if time.time() >= deadline:
        return {"exit_code": None, "timed_out": True}

    with log.open("wb") as output:
        process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import os,sys; go=os.read(0,1); "
                'os.execvp(sys.argv[1], sys.argv[1:]) if go==b"1" else sys.exit(125)',
                *command,
            ],
            cwd=cwd,
            stdin=subprocess.PIPE,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env=env,
        )
        released = False
        try:
            state["process"] = {
                "pid": process.pid,
                "identity": identity(process.pid),
                "boot_identity": boot_identity(),
                "children": [],
            }
            save()
            payload = b"1" + (stdin.read().encode() if stdin is not None else b"")
            while True:
                refresh(state["process"], save)
                if cancel_requested is not None and cancel_requested():
                    result = {"exit_code": None, "timed_out": False, "cancelled": True}
                    break

                remaining = deadline - time.time()
                if remaining <= 0:
                    result = {"exit_code": None, "timed_out": True}
                    break

                try:
                    released = True
                    process.communicate(input=payload, timeout=min(0.2, remaining))
                    result = {"exit_code": process.returncode, "timed_out": False}
                    break
                except subprocess.TimeoutExpired:
                    payload = None
        finally:
            if released:
                stop(state["process"], save, process)
            else:
                # Exit without exec on EOF if the start gate never receives permission.
                process.stdin.close()
                process.wait(timeout=2)

            state["process"] = None
            save()

    return result
