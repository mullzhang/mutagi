"""Preserve synthetic results through the real catalog registration path."""

import json

from mutagi.execution.experiment import Runner, create
from mutagi.execution.state import lock
from mutagi.project.repository import git
from mutagi.prompts import record_template

PART = "naedo/search.py"


def preserve_experiment(
    repo,
    *,
    title="Cached search | comparison",
    status="observed",
    limits="Only small inputs; 日本語",
    parent=None,
):
    options = (
        {}
        if parent is None
        else {
            "parent_id": parent["id"],
            "parent_record": parent["result"]["record"],
        }
    )
    with lock(repo):
        directory = create(
            repo,
            parent["saved_commit"] if parent else "HEAD",
            "Synthetic harvesting fixture; no model.",
            "test-model",
            "medium",
            300,
            10,
            **options,
        )
    job = Runner(directory)
    job.worktree.parent.mkdir(parents=True, exist_ok=True)
    git(
        repo,
        "worktree",
        "add",
        "-b",
        job.state["branch"],
        str(job.worktree),
        job.state["base"],
    )
    implementation_path = job.worktree / PART
    implementation_path.parent.mkdir(parents=True, exist_ok=True)
    implementation_path.write_text(
        f"# {job.state['id']}\ndef search(items):\n    return sorted(set(items))\n",
        encoding="utf-8",
    )
    git(job.worktree, "add", "--", PART)
    git(job.worktree, "commit", "-m", "test: save synthetic implementation")
    implementation = git(job.worktree, "rev-parse", "HEAD")
    observation = job.directory()
    observation.mkdir(parents=True)
    record = (observation / "README.md").relative_to(job.worktree).as_posix()
    content = (
        record_template()
        .format(
            id=job.state["id"],
            started_at=job.state["started_at"],
            mode="inherited" if parent else "independent",
            parent_ids=json.dumps(job.state["parent_ids"]),
            base_commit=job.state["base"],
        )
        .replace(
            "implementation_commit: null", "implementation_commit: " + implementation, 1
        )
        .replace("status: unresolved", "status: " + status, 1)
        .replace("# <Name of the question>", "# " + title, 1)
    )
    (job.worktree / record).write_text(content, encoding="utf-8")
    (observation / "input.bin").write_bytes(b"\x00\xffinput")
    git(job.worktree, "add", "--", str(observation))
    git(job.worktree, "commit", "-m", "test: save synthetic observations")
    job.state.update(
        phase="saved",
        saved_commit=git(job.worktree, "rev-parse", "HEAD"),
        reason="completed",
        result={
            "title": title,
            "summary": "A finite comparison of repeated requests",
            "limits": limits,
            "checks": "Synthetic fixture; no model or actual benchmark",
            "record": record,
            "implementation_commit": implementation,
            "status": status,
        },
    )
    job.save()
    job.register()
    return job.state
