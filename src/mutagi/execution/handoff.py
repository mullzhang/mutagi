"""Preserve App results after the controller supplies an observed terminal state."""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
from pathlib import PurePosixPath

from mutagi.execution.experiment import Runner
from mutagi.project.repository import git
from mutagi.runtime.process import recover_process
from mutagi.runtime.storage import atomic_json


def verify_files(job, receipt, *, commit=None):
    directory = job.directory()
    files = receipt["files"]
    if not isinstance(files, dict) or not files:
        raise ValueError("a nonempty evidence inventory is required")

    for name, expected in files.items():
        relative = PurePosixPath(name)
        path = job.worktree / relative
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or not job.allowed(name, directory)
            or any(p.is_symlink() for p in (path, *path.parents))
            or not path.resolve().is_relative_to(job.worktree.resolve())
        ):
            raise ValueError(f"invalid evidence path: {name}")

        data = (
            subprocess.check_output(
                ["git", "-C", str(job.worktree), "show", f"{commit}:{name}"]
            )
            if commit
            else path.read_bytes()
        )
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError(f"evidence changed: {name}")


def verify_inventory(job, receipt, *, generated):
    paths = set(job.changed())
    # Include ignored files so unlisted logs block the handoff.
    others = (
        subprocess.check_output(
            [
                "git",
                "-C",
                str(job.worktree),
                "ls-files",
                "--others",
                "-z",
            ]
        )
        .decode()
        .split("\0")
    )
    paths.update(p for p in others if p)
    permitted = set(receipt["files"])
    if generated:
        prefix = job.directory().relative_to(job.worktree).as_posix() + "/_runner/"
        permitted.update(prefix + name for name in ("receipt.json", "app-handoff.json"))

    if paths - permitted or set(receipt["files"]) - paths:
        raise ValueError(
            "the evidence inventory does not match changed and untracked files"
        )


def accept(run, observation):
    job = Runner(run)
    receipt_path = run / "receipt.json"
    receipt = json.loads(receipt_path.read_text())
    receipt_hash = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
    s = job.state
    if (run / "events.jsonl").exists():
        raise ValueError("an App handoff cannot import CLI events")

    if receipt["id"] != s["id"] or receipt["thread_id"] != s["thread_id"]:
        raise ValueError("receipt identity differs from the assigned App thread")

    if s["phase"] not in (
        "awaiting_app",
        "collecting",
        "saving",
        "saved",
        "registered",
    ):
        raise ValueError("an App handoff cannot prepare or run an agent")

    if s["process"] is not None:
        if s["phase"] == "awaiting_app":
            raise ValueError("recover the recorded process before the App handoff")

        recover_process(s, job.save)

    if s["phase"] == "awaiting_app":
        if (
            observation["thread_id"] != s["thread_id"]
            or observation["thread_status"] != "idle"
            or observation["turn_status"] != "completed"
            or not observation["turn_id"]
            or not observation["observed_at"]
        ):
            raise ValueError(
                "the controller must confirm completion of the assigned App turn and an idle thread"
            )

        verify_files(job, receipt)
        verify_inventory(job, receipt, generated=False)

        if git(job.worktree, "branch", "--show-current") != s["branch"]:
            raise ValueError("the assigned branch changed")

        job.load_result(job.directory())
        s.update(
            phase="collecting",
            reason="completed",
            receipt_sha256=receipt_hash,
            result_sha256=hashlib.sha256(
                (run / "result.json").read_bytes()
            ).hexdigest(),
            app_observation=observation,
        )
        job.save()
    elif s["receipt_sha256"] != receipt_hash:
        raise ValueError("the accepted receipt changed; inspect it before recovery")

    if (
        hashlib.sha256((run / "result.json").read_bytes()).hexdigest()
        != s["result_sha256"]
    ):
        raise ValueError("the accepted result changed; inspect it before recovery")

    verify_files(job, receipt)
    # Collection may write the receipt before recording the preservation checkpoint.
    # Initial acceptance above still rejects every unlisted generated file.
    verify_inventory(job, receipt, generated=True)
    if s["phase"] == "collecting":
        # Force-add only explicitly listed paths, including required logs.
        subprocess.run(
            [
                "git",
                "-C",
                str(job.worktree),
                "add",
                "-f",
                "--",
                *receipt["files"],
            ],
            check=True,
        )
        job.collect()
        if s["reason"] != "completed":
            raise ValueError(
                "App results could not be collected; inspect them before preservation"
            )

    job.local_deadline = time.time() + 60
    if s["phase"] == "saving":
        atomic_json(
            job.directory() / "_runner" / "app-handoff.json",
            {
                "receipt": receipt,
                "app_observation": s["app_observation"],
                "receipt_sha256": s["receipt_sha256"],
                "result_sha256": s["result_sha256"],
            },
        )
        job.preserve()

    verify_files(job, receipt, commit=s["saved_commit"])
    if s["phase"] in ("saved", "registered"):
        job.register()

    atomic_json(
        run / "handoff-result.json",
        {
            "id": s["id"],
            "phase": s["phase"],
            "thread_id": s["thread_id"],
            "app_observation": s["app_observation"],
            "files_verified": len(receipt["files"]),
            "saved_commit": s["saved_commit"],
            "registry_commit": s["registry_commit"],
        },
    )
    return s
