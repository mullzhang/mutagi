from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from mutagi.project.repository import git

spec = importlib.util.spec_from_file_location(
    "check_release", Path(__file__).resolve().parents[2] / "scripts/check_release.py"
)


release = importlib.util.module_from_spec(spec)


spec.loader.exec_module(release)


def test_version_tag_must_match_both_project_and_lockfile(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "mutagi"\nversion = "1.2.3"\n'
    )
    lock = tmp_path / "uv.lock"
    lock.write_text('[[package]]\nname = "mutagi"\nversion = "1.2.3"\n')
    assert release.validate_version(tmp_path, "v1.2.3") == "1.2.3"
    with pytest.raises(ValueError, match="release tag must"):
        release.validate_version(tmp_path, "v1.2.4")
    lock.write_text('[[package]]\nname = "mutagi"\nversion = "1.2.2"\n')
    with pytest.raises(ValueError, match="versions differ"):
        release.validate_version(tmp_path, "v1.2.3")


def test_release_requires_tagged_clean_commit(empty_repo):
    path = empty_repo / "file.txt"
    path.write_text("first")
    git(empty_repo, "add", ".")
    git(empty_repo, "commit", "-m", "first")
    git(empty_repo, "tag", "v1.2.3")
    release.verify_tag(empty_repo, "v1.2.3")
    path.write_text("second")
    with pytest.raises(ValueError, match="commit the intended"):
        release.verify_tag(empty_repo, "v1.2.3")
    git(empty_repo, "add", ".")
    git(empty_repo, "commit", "-m", "second")
    with pytest.raises(ValueError, match="must point"):
        release.verify_tag(empty_repo, "v1.2.3")
