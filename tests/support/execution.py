from __future__ import annotations

import json

import mutagi.execution.experiment as experiment
import mutagi.execution.state as execution_state


def completed_jobs(repo, state):
    return [
        json.loads((repo / ".mutagi/runs" / item["id"] / "state.json").read_text())
        for item in state["jobs"]
    ]


def create(repo, tmp_path, **kw):
    task = tmp_path / "task.txt"
    task.write_text("Choose one question.")
    with execution_state.lock(repo):
        path = experiment.create(
            repo,
            "HEAD",
            task.read_text(),
            "gpt-6-astra",
            "medium",
            kw.pop("seconds", 30),
            kw.pop("reserve", 5),
            **kw,
        )

    return experiment.Runner(path)
