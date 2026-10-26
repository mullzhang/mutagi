from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import mutagi.execution.experiment as experiment
import mutagi.execution.state as execution_state
from mutagi.commands.inputs import read_plan
from mutagi.execution.plans import validate_round, validate_series
from mutagi.project.repository import git
from mutagi.prompts import record_template
from tests.support.repositories import commit


def test_sample_can_be_copied_tested_and_prepared(tmp_path):
    root = tmp_path / "copied-example"
    source = Path(__file__).resolve().parents[2] / "examples/minimal-python"
    shutil.copytree(source, root)
    subprocess.run(
        ["git", "init", "-b", "main", str(root)], check=True, capture_output=True
    )
    git(root, "config", "user.name", "Test")
    git(root, "config", "user.email", "test@example.invalid")
    subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    commit(root)
    for kind, validate in (("round", validate_round), ("series", validate_series)):
        validate(
            read_plan(
                kind,
                plan=str(root / f".mutagi/plans/{kind}.json"),
                steps=[],
                rounds=None,
                overrides={"model": "chosen-model"},
            )
        )
    with execution_state.lock(root):
        directory = experiment.create(
            root,
            "HEAD",
            (root / ".mutagi/plans/first.txt").read_text(),
            "chosen-model",
            "medium",
            60,
            10,
        )
    job = experiment.Runner(directory)
    job.prepare()
    assert job.state["phase"] == "ready"
    assert record_template().partition("\n---\n")[2] in job.prompt()
    assert f"id: {job.state['id']}\n" in job.prompt()
