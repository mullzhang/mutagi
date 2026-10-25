from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from mutagi.project.repository import git

PROJECT_CONFIG = Path(__file__).resolve().parents[1] / "fixtures/project.toml"


def create_empty_repository(tmp_path):
    root = tmp_path / "sample"
    subprocess.run(
        ["git", "init", "-b", "main", str(root)], check=True, capture_output=True
    )
    git(root, "config", "user.name", "Test")
    git(root, "config", "user.email", "test@example.invalid")
    return root


def create_repository(tmp_path):
    """Given an isolated real Git repository with no network preparation."""
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(
        ["git", "init", "-b", "main", str(root)], check=True, capture_output=True
    )
    for key, value in [("user.name", "Test"), ("user.email", "test@example.invalid")]:
        git(root, "config", key, value)

    (root / ".gitignore").write_text(".mutagi/runs/\n.mutagi/worktrees/\n")
    (root / "apm.lock.yaml").write_text(
        "dependencies: []\nlocal_deployed_file_hashes:\n"
        "  .claude/rules/naedo.md: sha256:" + "0" * 64 + "\n"
    )
    for name in (
        "AGENTS.md",
        "docs/operations.md",
        ".mutagi/prompts/explore.md",
        ".mutagi/prompts/inherit.md",
        ".mutagi/prompts/integrate.md",
        "experiments/INDEX.md",
        "knowledge/notes/seed.md",
    ):
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# Reference\n")

    config = PROJECT_CONFIG.read_text().replace("PYTHON", sys.executable)
    (root / ".mutagi/config.toml").write_text(config)
    git(root, "add", ".")
    git(root, "commit", "-m", "feat: seed")
    return root


def snapshot(root):
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def commit(root):
    git(root, "add", ".")
    git(root, "commit", "-m", "test: configure project")


def write_defaults(repo):
    path = repo / ".mutagi/config.toml"
    path.write_text(
        path.read_text()
        + """
[execution]
model = "common-model"
effort = "medium"
[execution.experiment]
seconds = 90
reserve_seconds = 15
[execution.round]
model = "round-model"
seconds = 600
experiment_seconds = 60
integration_seconds = 60
reserve_seconds = 10
[execution.series]
seconds = 900
round_seconds = 300
experiment_seconds = 60
integration_seconds = 60
reserve_seconds = 10
"""
    )
    commit(repo)
    return git(repo, "rev-parse", "HEAD")
