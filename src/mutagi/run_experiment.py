#!/usr/bin/env python3
"""Run and preserve one exploration. Requires Python 3.12+ and POSIX."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tarfile
import time
from uuid import UUID, uuid4

from mutagi.runner_process import recover_process, run_job
from mutagi.prompts import record_template, render_prompt
from mutagi.project import matches
from mutagi.inputs import EFFORTS, read_text, resolve_settings, validate_task
from mutagi.settings import EXECUTION_FIELDS
from mutagi.status import add_status_arguments, show_execution
from mutagi.repository import git, require_clean, require_ready

SCHEMA = {
    "type": "object",
    "properties": {
        name: {"type": "string"}
        for name in (
            "title",
            "summary",
            "limits",
            "record",
            "implementation_commit",
            "checks",
        )
    }
    | {
        "status": {
            "type": "string",
            "enum": ["observed", "refuted", "unresolved", "blocked"],
        }
    },
    "required": [
        "title",
        "summary",
        "limits",
        "record",
        "implementation_commit",
        "checks",
        "status",
    ],
    "additionalProperties": False,
}


def atomic_json(path: Path, value) -> None:
    temporary = path.with_suffix(".tmp")
    with temporary.open("w") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())

    temporary.replace(path)


def utc(epoch: float) -> str:
    return (
        datetime.fromtimestamp(epoch, timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def validate_id(value: str) -> str:
    parsed = UUID(value)
    if parsed.version != 4 or str(parsed) != value:
        raise ValueError("the ID must be a full lowercase UUIDv4")

    return value


@contextmanager
def lock(repo: Path):
    store = repo / ".mutagi/runs"
    store.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (store / "lock").open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("another runner is using this repository") from None

        yield


def service_pause_requested(repo, series_id):
    path = repo / ".mutagi/runs/service.json"
    return (
        series_id is not None
        and (repo / ".mutagi/runs/service.pause").exists()
        and path.exists()
        and json.loads(path.read_text())["id"] == series_id
    )


def require_series_owner(repo, series_id=None):
    for path in (repo / ".mutagi/runs" / "series").glob("*/state.json"):
        state = json.loads(path.read_text())
        if (
            state["phase"]
            not in (
                "completed",
                "budget_exhausted",
                "integration_deferred",
                "usage_exhausted",
                "usage_unknown",
            )
            and state["id"] != series_id
        ):
            raise RuntimeError(
                "resume the unfinished series before starting another run"
            )


def validate_input(task_text, model, effort, seconds, reserve):
    validate_task(task_text)
    if not isinstance(model, str) or not model.strip() or effort not in EFFORTS:
        raise ValueError("specify a nonempty model and a supported reasoning effort")
    if not (
        type(seconds) in (int, float)
        and type(reserve) in (int, float)
        and math.isfinite(seconds)
        and math.isfinite(reserve)
        and 0 < reserve < seconds
    ):
        raise ValueError(
            "budget values must be finite and satisfy 0 < reserve < total seconds"
        )


def validate_parent(repo, base, parent_id, parent_record):
    if bool(parent_id) != bool(parent_record):
        raise ValueError("inheritance requires both --parent-id and --parent-record")

    if parent_id:
        validate_id(parent_id)
        content = git(repo, "show", base + ":" + parent_record)
        if not re.search(r"^id: " + re.escape(parent_id) + r"$", content, re.M):
            raise ValueError(
                "the parent record at the base commit does not match its ID"
            )


def create(
    repo: Path,
    base: str,
    task_text: str,
    model: str,
    effort: str,
    seconds: float,
    reserve: float,
    parent_id=None,
    parent_record=None,
    identifier=None,
    started=None,
    series_id=None,
):
    validate_input(task_text, model, effort, seconds, reserve)

    require_series_owner(repo, series_id)
    shared_branch, base, project = require_ready(repo, base)
    validate_parent(repo, base, parent_id, parent_record)

    for previous in (repo / ".mutagi/runs").glob("*/state.json"):
        if json.loads(previous.read_text())["phase"] != "registered":
            raise RuntimeError(
                f"finish the unfinished run before starting another: {previous.parent.name}"
            )

    identifier = str(uuid4()) if identifier is None else validate_id(identifier)
    run = repo / ".mutagi/runs" / identifier
    if run.exists():
        # A round records the ID before allocation. No process can start before
        # state.json exists, so only this incomplete allocation can be resumed.
        expected = {"task.txt", "schema.json", "schema.tmp", "state.tmp"}
        if (
            (repo / ".mutagi/worktrees" / identifier).exists()
            or any(p.name not in expected for p in run.iterdir())
            or (
                (run / "task.txt").exists()
                and read_text(run / "task.txt") != task_text
            )
        ):
            raise RuntimeError("the existing run is not an incomplete allocation")
    else:
        run.mkdir(mode=0o700)

    started = time.time() if started is None else started
    state = {
        "id": identifier,
        "project": project,
        "repo": str(repo),
        "base": base,
        "shared_branch": shared_branch,
        "registry_base": git(repo, "rev-parse", "HEAD"),
        "branch": "codex/explore-" + identifier,
        "worktree": str(repo / ".mutagi/worktrees" / identifier),
        "started_at": utc(started),
        "exploration_deadline": started + seconds - reserve,
        "deadline": started + seconds,
        "model": model,
        "effort": effort,
        "seconds": seconds,
        "reserve_seconds": reserve,
        "parent_ids": [parent_id] if parent_id else [],
        "parent_record": parent_record,
        "phase": "preparing",
        "prepared": 0,
        "process": None,
        "reason": None,
        "result": None,
        "thread_id": None,
    }
    if series_id is not None:
        state["series_id"] = series_id

    (run / "task.txt").write_text(task_text)
    atomic_json(run / "schema.json", SCHEMA)
    atomic_json(run / "state.json", state)
    return run


class Runner:
    def __init__(self, run: Path):
        self.run = run
        self.state = json.loads((run / "state.json").read_text())
        self.repo = Path(self.state["repo"])
        self.project = self.state["project"]
        self.worktree = Path(self.state["worktree"])
        self.local_deadline = self.state["deadline"]

    def save(self):
        atomic_json(self.run / "state.json", self.state)

    def prepare(self):
        s = self.state
        if not self.worktree.exists():
            self.worktree.parent.mkdir(exist_ok=True)
            # Inspect an interrupted git worktree add without overwriting its state.
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(self.repo),
                    "worktree",
                    "add",
                    "-b",
                    s["branch"],
                    str(self.worktree),
                    s["base"],
                ],
                check=True,
                timeout=30,
            )

        if git(self.worktree, "rev-parse", "HEAD") != s["base"]:
            raise RuntimeError("HEAD during preparation differs from the fixed base revision")

        if git(self.worktree, "branch", "--show-current") != s["branch"]:
            raise RuntimeError("the exploration branch differs from the expected branch")

        # Verify write access to Git metadata before consuming model usage.
        probe = (
            Path(git(self.worktree, "rev-parse", "--absolute-git-dir"))
            / "forest-write-probe"
        )
        with probe.open("x"):
            pass

        probe.unlink()
        preparation = self.project["preparation"]
        for i in range(s["prepared"], len(preparation)):
            outcome = run_job(
                preparation[i],
                self.worktree,
                self.run / f"prepare-{i}.log",
                s["exploration_deadline"],
                s,
                self.save,
            )
            if outcome["exit_code"] != 0:
                raise RuntimeError(f"preparation failed: {preparation[i]}; {outcome}")

            s["prepared"] = i + 1
            self.save()

        require_clean(self.worktree)
        s["instruction_hashes"] = {
            name: hashlib.sha256((self.worktree / name).read_bytes()).hexdigest()
            for name in [
                ".mutagi/config.toml",
                *self.project["references"],
                self.project["prompts"]["inherit" if s["parent_ids"] else "explore"],
            ]
        }
        s["phase"] = "ready"
        self.save()

    def prompt(self):
        s = self.state
        mode = "inherit" if s["parent_ids"] else "explore"
        return render_prompt(
            "explore",
            state=s,
            project=self.project,
            exploration_kind="inherited" if s["parent_ids"] else "independent",
            project_prompt=self.project["prompts"][mode],
            exploration_deadline=utc(s["exploration_deadline"]),
            deadline=utc(s["deadline"]),
            task=read_text(self.run / "task.txt"),
            exploration_paths=[
                path.format(id=s["id"]) for path in self.project["exploration_paths"]
            ],
            folder_time=s["started_at"].replace(":", ""),
            record_template=record_template(),
        )

    def execute(self):
        s = self.state
        (self.run / "prompt.txt").write_text(self.prompt())
        command = [
            "codex",
            "exec",
            "--json",
            "--approve-for-me",
            "--model",
            s["model"],
            "-c",
            "model_reasoning_effort=" + s["effort"],
            "--output-schema",
            str(self.run / "schema.json"),
            "--output-last-message",
            str(self.run / "result.json"),
            "-",
        ]
        if "usage_budget" in s:
            command = [
                sys.executable,
                "-m",
                "mutagi.codex_session",
                str(self.run),
            ]

        s["phase"] = "executing"
        self.save()  # Recovery after this point must not start another agent.
        with (self.run / "prompt.txt").open() as prompt:
            outcome = run_job(
                command,
                self.worktree,
                self.run / "events.jsonl",
                s["exploration_deadline"],
                s,
                self.save,
                stdin=prompt,
                cancel_requested=lambda: service_pause_requested(
                    self.repo, s.get("series_id")
                ),
            )

        s["reason"] = (
            "interrupted"
            if outcome.get("cancelled")
            else "time_limit"
            if outcome["timed_out"]
            else ("completed" if outcome["exit_code"] == 0 else "agent_failed")
        )
        if "usage_budget" in s and (self.run / "usage.json").exists():
            usage = json.loads((self.run / "usage.json").read_text())
            if usage.get("stop_reason") is not None:
                s["reason"] = usage["stop_reason"]

        s["exit"] = outcome
        s["phase"] = "collecting"
        self.save()

    def changed(self):
        tracked = (
            subprocess.check_output(
                [
                    "git",
                    "-C",
                    str(self.worktree),
                    "diff",
                    "--name-only",
                    "-z",
                    self.state["base"],
                ],
            )
            .decode()
            .split("\0")
        )
        untracked = (
            subprocess.check_output(
                [
                    "git",
                    "-C",
                    str(self.worktree),
                    "ls-files",
                    "--others",
                    "--exclude-standard",
                    "-z",
                ],
            )
            .decode()
            .split("\0")
        )
        return sorted(set(tracked + untracked) - {""})

    def directory(self):
        s = self.state
        prefix = s["started_at"].replace(":", "")
        pattern = re.compile(
            re.escape(prefix) + r"_[a-z]+(?:-[a-z]+)*_" + re.escape(s["id"])
        )
        found = [
            p
            for p in (self.worktree / self.project["experiments"]).glob("*_" + s["id"])
            if p.is_dir() and not p.is_symlink() and pattern.fullmatch(p.name)
        ]
        if len(found) > 1:
            raise RuntimeError("multiple experiment directories use this ID")

        return (
            found[0]
            if found
            else self.worktree
            / self.project["experiments"]
            / f"{prefix}_interrupted_{s['id']}"
        )

    def allowed(self, path, directory):
        if path in self.project["generated_hashes"]:
            if not (self.worktree / path).is_file():
                return False

            original = git(self.worktree, "show", f"{self.state['base']}:{path}")
            current = (self.worktree / path).read_text().strip()
            for deployed in self.project["generated_hashes"][path]:
                pattern = r"(?m)^  " + re.escape(deployed) + r": sha256:[0-9a-f]{64}$"
                if (
                    len(re.findall(pattern, original)) != 1
                    or len(re.findall(pattern, current)) != 1
                ):
                    return False

                original = re.sub(pattern, "", original)
                current = re.sub(pattern, "", current)

            return original == current

        return matches(
            path, self.project["exploration_paths"], self.state["id"]
        ) or path.startswith(directory.relative_to(self.worktree).as_posix() + "/")

    def snapshot(self, paths):
        recovery = self.run / "recovery"
        recovery.mkdir(exist_ok=True)
        target = recovery / str(uuid4())
        target.mkdir()
        with (target / "tracked.patch").open("wb") as stream:
            if paths:
                subprocess.run(
                    [
                        "git",
                        "-C",
                        str(self.worktree),
                        "diff",
                        "--binary",
                        self.state["base"],
                        "--",
                        *paths,
                    ],
                    stdout=stream,
                    check=True,
                    timeout=30,
                )

        with tarfile.open(target / "files.tar", "w", dereference=False) as archive:
            for name in paths:
                p = self.worktree / name
                if p.is_file() or p.is_symlink():
                    archive.add(p, arcname=name, recursive=False)

        atomic_json(
            target / "hashes.json",
            {
                p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in (target / "tracked.patch", target / "files.tar")
            },
        )

    def load_result(self, directory):
        s = self.state
        result = json.loads((self.run / "result.json").read_text())
        if (
            not isinstance(result, dict)
            or set(result) != set(SCHEMA["required"])
            or any(not isinstance(v, str) for v in result.values())
        ):
            raise ValueError("final result fields do not match the schema")

        if result["status"] not in SCHEMA["properties"]["status"]["enum"]:
            raise ValueError("unknown result status")

        if (
            result["record"]
            != (directory / "README.md").relative_to(self.worktree).as_posix()
        ):
            raise ValueError("the final record must be this experiment's README.md")

        record = self.worktree / result["record"]
        if (
            record.is_symlink()
            or not record.resolve().is_relative_to(directory.resolve())
            or not record.is_file()
        ):
            raise ValueError("the record must be a regular file within this experiment")

        content = record.read_text()
        for pattern in (
            r"^id: " + s["id"] + r"$",
            r"^base_commit: " + s["base"] + r"$",
            r"^human_decision: unreviewed$",
            r"^status: " + result["status"] + r"$",
            r"^started_at: '" + re.escape(s["started_at"]) + r"'$",
        ):
            if not re.search(pattern, content, re.M):
                raise ValueError("observation record metadata is inconsistent")

        mode = "inherited" if s["parent_ids"] else "independent"
        if not re.search(r"^mode: " + mode + r"$", content, re.M):
            raise ValueError("the record mode differs from the assigned mode")

        parents = re.search(r"^parent_ids: \[(.*)\]$", content, re.M)
        if parents is None:
            raise ValueError("the record requires parent_ids in inline form")

        actual_parents = [
            p.strip().strip("\"'") for p in parents[1].split(",") if p.strip()
        ]
        if actual_parents != s["parent_ids"]:
            raise ValueError("the record parents differ from the assigned parents")

        implementation = result["implementation_commit"]
        if implementation and not re.search(
            r"^implementation_commit: " + implementation + r"$", content, re.M
        ):
            raise ValueError("the recorded implementation revision differs from the reported commit")

        if (
            implementation or result["status"] in ("observed", "refuted")
        ) and not re.fullmatch("[0-9a-f]{40}", implementation):
            raise ValueError("an observed result requires a fixed implementation commit")

        if implementation:
            for before, after in (
                (s["base"], implementation),
                (implementation, "HEAD"),
            ):
                subprocess.run(
                    [
                        "git",
                        "-C",
                        str(self.worktree),
                        "merge-base",
                        "--is-ancestor",
                        before,
                        after,
                    ],
                    check=True,
                )

        return result

    def collect(self):
        s = self.state
        if not self.worktree.exists():
            raise RuntimeError(
                "the working tree is missing; resolve preparation permissions before recovery"
            )

        if git(self.worktree, "branch", "--show-current") != s["branch"]:
            raise RuntimeError("the exploration changed its assigned branch")

        subprocess.run(
            [
                "git",
                "-C",
                str(self.worktree),
                "merge-base",
                "--is-ancestor",
                s["base"],
                "HEAD",
            ],
            check=True,
        )
        directory = self.directory()
        paths = self.changed()
        disallowed = [
            p
            for p in paths
            if not self.allowed(p, directory)
            or not (self.worktree / p).resolve().is_relative_to(self.worktree.resolve())
        ]
        # Preserve recoverable results even when prohibited paths block publication.
        self.snapshot([p for p in paths if self.allowed(p, directory)])
        if disallowed:
            raise RuntimeError(f"out-of-scope changes require review: {disallowed}")

        self.read_session_id()

        result = None
        if s["reason"] == "completed":
            try:
                if s["thread_id"] is None:
                    raise ValueError("no Codex session ID was recorded")

                result = self.load_result(directory)
            except (ValueError, OSError, subprocess.CalledProcessError) as error:
                s["reason"] = "invalid_result"
                s["result_error"] = str(error)

        directory.mkdir(parents=True, exist_ok=True)
        if result is None:
            record = directory / "RUNNER-RECOVERY.md"
            record.write_text(f"""---
id: {s["id"]}
started_at: '{s["started_at"]}'
mode: {"inherited" if s["parent_ids"] else "independent"}
parent_ids: {json.dumps(s["parent_ids"])}
base_commit: {s["base"]}
implementation_commit: null
status: {"blocked" if s["reason"] == "preparation_failed" else "unresolved"}
human_decision: unreviewed
---

# Interrupted run recovered by the controller

Exit reason: {s["reason"]}. Successful observation has not been verified.
Remaining changes, inputs, and records are kept on this preservation branch. The observed implementation revision is unverified.
Recovery location for execution logs, binary diffs, and untracked files: {self.run}
Exploration is not restarted automatically. Check preserved contents and the remaining budget before resuming.
""")
            result = {
                "title": "Interrupted exploration",
                "summary": "The controller recovered the known results and changes",
                "limits": s["reason"],
                "record": record.relative_to(self.worktree).as_posix(),
                "implementation_commit": "",
                "checks": "Observation results are unverified",
                "status": "blocked"
                if s["reason"] == "preparation_failed"
                else "unresolved",
            }

        s["result"] = result
        receipt = directory / "_runner"
        receipt.mkdir(exist_ok=True)
        atomic_json(
            receipt / "receipt.json",
            {
                k: s[k]
                for k in (
                    "id",
                    "base",
                    "started_at",
                    "parent_ids",
                    "branch",
                    "model",
                    "effort",
                    "exploration_deadline",
                    "deadline",
                    "thread_id",
                    "reason",
                    "result",
                )
            }
            | {
                "logs": str(self.run),
                "instruction_hashes": s.get("instruction_hashes", {}),
                "verification": "Preservation structure checked. Reproduction and human adoption are unverified.",
            },
        )
        s["phase"] = "saving"
        self.save()

    def read_session_id(self):
        events = self.run / "events.jsonl"
        if events.exists():
            for line in events.read_text(errors="replace").splitlines():
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if isinstance(event, dict) and event.get("type") == "thread.started":
                    self.state["thread_id"] = event["thread_id"]

    def preservation_message(self):
        return (
            "docs(experiment): preserve runner outcome",
            f"Preserve {self.state['id']} with {self.state['reason']} outcome.\n\n"
            "Validation: scope, saved refs, record metadata and git diff --check. "
            "Scientific reproduction and human adoption remain separate.",
        )

    def local(self, command, cwd, name):
        log = self.run / f"{name}-{uuid4()}.log"
        outcome = run_job(command, cwd, log, self.local_deadline, self.state, self.save)
        if outcome["exit_code"] != 0:
            raise RuntimeError(
                f"local preservation failed: {name}; {outcome}; log={log}"
            )

    def preserve(self):
        s = self.state
        paths = self.changed()
        directory = self.directory()
        if any(not self.allowed(p, directory) for p in paths):
            raise RuntimeError("the allowed scope changed during preservation")

        # Include the recovery record before git commit, which may fail.
        self.snapshot(paths)
        subprocess.run(["git", "-C", str(self.worktree), "diff", "--check"], check=True)
        if git(self.worktree, "status", "--porcelain"):
            title, body = self.preservation_message()
            self.local(
                ["git", "-C", str(self.worktree), "add", "--", *paths],
                self.worktree,
                "stage",
            )
            subprocess.run(
                ["git", "-C", str(self.worktree), "diff", "--check", s["base"]],
                check=True,
                timeout=30,
            )
            self.local(
                [
                    "git",
                    "-C",
                    str(self.worktree),
                    "commit",
                    "-m",
                    title,
                    "-m",
                    body,
                ],
                self.worktree,
                "save",
            )

        require_clean(self.worktree)
        s["saved_commit"] = git(self.worktree, "rev-parse", "HEAD")
        s["phase"] = "saved"
        self.save()

    def register(self):
        s = self.state
        if git(self.repo, "rev-parse", s["branch"]) != s["saved_commit"]:
            raise RuntimeError("the saved exploration branch moved before registration")

        git(
            self.repo, "cat-file", "-e", s["saved_commit"] + ":" + s["result"]["record"]
        )
        if git(self.repo, "branch", "--show-current") != s["shared_branch"]:
            raise RuntimeError(
                "the shared branch changed; return to the recorded branch"
            )

        index = self.repo / self.project["catalog"]
        marker = f"<!-- forest-run:{s['id']} -->"
        # Retrying after the registration commit must not duplicate entries or commits.
        tracked = git(self.repo, "show", "HEAD:" + self.project["catalog"])
        if marker in tracked:
            blob = subprocess.check_output(
                ["git", "-C", str(self.repo), "show", "HEAD:" + self.project["catalog"]]
            )
            if hashlib.sha256(blob).hexdigest() != s["registry_hash"]:
                raise RuntimeError("the committed registration differs from the prepared entry")

            s["registry_commit"] = git(self.repo, "rev-parse", "HEAD")
            s["phase"] = "registered"
            self.save()
            return

        current = index.read_text()
        if marker not in current:
            require_clean(self.repo)
            result = s["result"]

            def plain(value):
                return " ".join(value.split()).replace("|", "\\|")

            entry = f"""\n{marker}
## Automated run {s["id"]}

| Field | Value |
| --- | --- |
| Title | {plain(result["title"])} |
| Start time (UTC) and parents | {s["started_at"]}; {", ".join(s["parent_ids"]) or "none"} |
| Base revision | `{s["base"]}` |
| Saved revision and branch | `{s["saved_commit"]}`、`{s["branch"]}` |
| Result and exit reason | {result["status"]}; {s["reason"]} |
| Findings (reported by the explorer) | {plain(result["summary"])} |
| Limitations | {plain(result["limits"])} |
| Checks (reported by the explorer) | {plain(result["checks"])} |
| Human decision | unreviewed. The controller checked preservation structure; observations have not been independently reproduced. |

Record and reproduction entry point:

```bash
git show {s["saved_commit"]}:{result["record"]}
```
"""
            content = current.rstrip() + "\n" + entry
            s["registry_hash"] = hashlib.sha256(content.encode()).hexdigest()
            self.save()
            index.write_text(content)
        else:
            changed = git(self.repo, "status", "--porcelain")
            if changed not in (
                "M " + self.project["catalog"],
                "M  " + self.project["catalog"],
            ):
                raise RuntimeError(
                    "registration cannot be recovered while the shared working tree has unrelated changes"
                )

            if hashlib.sha256(current.encode()).hexdigest() != s["registry_hash"]:
                raise RuntimeError("the registration text changed after an interrupted write")

        subprocess.run(["git", "-C", str(self.repo), "diff", "--check"], check=True)
        subprocess.run(
            ["git", "-C", str(self.repo), "add", "--", self.project["catalog"]],
            check=True,
        )
        self.local(
            [
                "git",
                "-C",
                str(self.repo),
                "commit",
                "-m",
                "docs(experiments): register automatic exploration",
                "-m",
                f"Register {s['id']} at {s['saved_commit']}.\n\nValidated saved record and scope; adoption remains unreviewed.",
            ],
            self.repo,
            "register",
        )
        s["registry_commit"] = git(self.repo, "rev-parse", "HEAD")
        s["phase"] = "registered"
        self.save()

    def advance(self, *, recovery=False):
        s = self.state
        if recovery:
            # Allow 60 seconds for local recovery without extending the model deadline.
            self.local_deadline = time.time() + 60

        recover_process(s, self.save)
        if recovery and s["phase"] == "executing":
            s["reason"] = "runner_interrupted"
            s["phase"] = "collecting"
            self.save()

        if s["phase"] == "preparing":
            try:
                self.prepare()
            except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
                if not self.worktree.exists():
                    raise

                s.update(
                    reason="preparation_failed", error=str(error), phase="collecting"
                )
                self.save()

        if s["phase"] == "ready":
            try:
                self.execute()
            except (OSError, KeyboardInterrupt) as error:
                s.update(reason="interrupted", error=str(error), phase="collecting")
                self.save()

        if s["phase"] == "collecting":
            recover_process(s, self.save)
            self.collect()

        if s["phase"] == "saving":
            self.preserve()

        if s["phase"] == "saved":
            self.register()

        return s


def main():
    parser = argparse.ArgumentParser(prog="mutagi experiment", description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    commands = parser.add_subparsers(dest="command", required=True)
    start = commands.add_parser("start")
    start.add_argument("--base", required=True)
    instruction = start.add_mutually_exclusive_group(required=True)
    instruction.add_argument("--task", help="Instruction file, or - for standard input")
    instruction.add_argument("--prompt", help="Instruction text for this exploration")
    start.add_argument("--model", default=argparse.SUPPRESS)
    start.add_argument(
        "--effort",
        default=argparse.SUPPRESS,
        choices=EFFORTS,
    )
    start.add_argument(
        "--seconds",
        type=float,
        default=argparse.SUPPRESS,
        help="Total seconds (default: saved setting, otherwise 3600)",
    )
    start.add_argument(
        "--reserve-seconds",
        type=float,
        default=argparse.SUPPRESS,
        help="Preservation reserve (default: saved setting, otherwise 600)",
    )
    start.add_argument("--parent-id")
    start.add_argument("--parent-record")
    start.add_argument(
        "--dry-run",
        action="store_true",
        help="Print resolved inputs as JSON without creating or running anything",
    )
    commands.add_parser("resume").add_argument("id", type=validate_id)
    add_status_arguments(commands.add_parser("status"), validate_id)

    args = parser.parse_args()
    if args.command == "start":
        task_text = validate_task(
            args.prompt if args.prompt is not None else read_text(args.task)
        )
    repo = Path(git(args.repo, "rev-parse", "--show-toplevel")).resolve()
    if os.name != "posix":
        parser.error("this runner requires POSIX process groups and flock")

    if args.command == "status":
        show_execution(repo, "experiment", args.id, raw=args.json)
        return

    if args.command == "start":
        settings = {
            "seconds": 3600,
            "reserve_seconds": 600,
            **resolve_settings(
                repo,
                args.base,
                "experiment",
                {
                    field: vars(args)[field]
                    for field in EXECUTION_FIELDS["experiment"]
                    if field in vars(args)
                },
            ),
        }
        if not {"model", "effort"} <= settings.keys():
            raise ValueError(
                "provide --model and --effort or save explicit execution defaults at the base commit"
            )
        validate_input(
            task_text,
            settings["model"],
            settings["effort"],
            settings["seconds"],
            settings["reserve_seconds"],
        )
        validate_parent(repo, settings["base"], args.parent_id, args.parent_record)
        if args.dry_run:
            print(
                json.dumps(
                    {
                        **settings,
                        "prompt": task_text,
                        "parent_id": args.parent_id,
                        "parent_record": args.parent_record,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return

    def interrupted(*_):
        raise KeyboardInterrupt("runner interrupted")

    signal.signal(signal.SIGTERM, interrupted)
    with lock(repo):
        if args.command == "start":
            run = create(
                repo,
                settings["base"],
                task_text,
                settings["model"],
                settings["effort"],
                settings["seconds"],
                settings["reserve_seconds"],
                args.parent_id,
                args.parent_record,
            )
        else:
            run = repo / ".mutagi/runs" / args.id

        print(f"Run: {run.name}", flush=True)
        runner = Runner(run)
        if runner.state.get("series_id") is not None:
            raise RuntimeError("use mutagi series resume for a run owned by a series")

        try:
            result = runner.advance(recovery=args.command == "resume")
        except (
            OSError,
            RuntimeError,
            ValueError,
            subprocess.SubprocessError,
            KeyboardInterrupt,
        ) as error:
            runner.state["error"] = str(error)
            runner.save()
            print(
                f"Incomplete: {error}\nRecovery command: mutagi experiment resume {run.name}"
            )
            raise SystemExit(1) from error

        print(
            json.dumps(
                {
                    "id": result["id"],
                    "phase": result["phase"],
                    "reason": result["reason"],
                    "saved_commit": result.get("saved_commit"),
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
