from __future__ import annotations

import pytest

import mutagi.execution.experiment as experiment
import mutagi.execution.state as execution_state
import mutagi.project.initialize as project_initialize
from mutagi.project.initialize import initialize
from mutagi.project.repository import git, project_at
from mutagi.prompts import record_template
from tests.support.repositories import commit, snapshot


@pytest.mark.parametrize("preset", list(project_initialize.PRESETS))
def test_explicit_preparation_presets_and_model_defaults(empty_repo, preset):
    for name in project_initialize.PRESETS[preset]["requires"]:
        (empty_repo / name).write_text("Project manifest fixture\n")
    before = snapshot(empty_repo)
    options = dict(preset=preset, execution={"model": "chosen-model", "effort": "high"})
    project_initialize.initialize(empty_repo, None, [], [], dry_run=True, **options)
    assert snapshot(empty_repo) == before
    project_initialize.initialize(empty_repo, None, [], [], **options)
    config = project_at(empty_repo)
    assert config["preparation"] == project_initialize.PRESETS[preset]["commands"]
    assert config["execution"] == {"model": "chosen-model", "effort": "high"}
    assert config["exploration_paths"] == ["knowledge/notes/"]


def test_preset_missing_manifest_rejects_before_writing(empty_repo):
    before = snapshot(empty_repo)
    with pytest.raises(ValueError, match="regular file"):
        project_initialize.initialize(empty_repo, None, [], [], preset="node-npm")
    assert snapshot(empty_repo) == before


def test_default_initialization_can_prepare_a_committed_project(empty_repo):
    initialize(empty_repo, None, [], [])
    config = project_at(empty_repo)
    assert config["name"] == "sample"
    assert config["exploration_paths"] == ["knowledge/notes/"]
    assert config["references"] == config["preparation"] == []
    assert not (empty_repo / ".mutagi/plans").exists()
    assert not (empty_repo / ".mutagi/runs").exists()
    commit(empty_repo)
    with execution_state.lock(empty_repo):
        directory = experiment.create(
            empty_repo, "HEAD", "Test one assumption.", "test-model", "medium", 60, 10
        )
    job = experiment.Runner(directory)
    job.prepare()
    assert job.state["phase"] == "ready"
    assert record_template() in job.prompt()
    assert git(empty_repo, "status", "--porcelain") == ""


@pytest.mark.parametrize(
    "name",
    [
        ".mutagi/config.toml",
        ".mutagi/prompts/inherit.md",
        "experiments/INDEX.md",
        "knowledge/README.md",
        ".mutagi",
    ],
)
def test_initializer_checks_all_collisions_before_writing(empty_repo, name):
    path = empty_repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("Existing user content")
    before = snapshot(empty_repo)
    with pytest.raises(ValueError, match="No files were changed"):
        initialize(empty_repo, None, [], [])
    assert snapshot(empty_repo) == before


@pytest.mark.parametrize("name", [".mutagi", "knowledge", ".gitignore"])
def test_initializer_rejects_symlinked_output(empty_repo, tmp_path, name):
    outside = tmp_path / "outside"
    if name == ".gitignore":
        outside.write_text("unrelated")
    else:
        outside.mkdir()
        (outside / "keep").write_text("unrelated")
    (empty_repo / name).symlink_to(outside)
    before = snapshot(empty_repo)
    with pytest.raises(ValueError, match="symlink"):
        initialize(empty_repo, None, [], [])
    assert snapshot(empty_repo) == before
    assert not (empty_repo / "experiments").exists()


def test_init_requires_repository_root_and_existing_references(empty_repo):
    child = empty_repo / "child"
    child.mkdir()
    before = snapshot(empty_repo)
    with pytest.raises(ValueError, match="repository root"):
        initialize(child, None, [], [])
    with pytest.raises(ValueError, match="regular file"):
        initialize(empty_repo, None, [], ["missing.md"])
    with pytest.raises(ValueError, match="project-relative"):
        initialize(empty_repo, None, ["../outside"], [])
    assert snapshot(empty_repo) == before
