from __future__ import annotations

import json
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest

from mutagi.execution import harvest
from mutagi.execution.catalog import catalog_entries, read_experiment
from mutagi.execution.harvest_records import BRANCH, REF, read_records
from mutagi.execution.state import lock
from mutagi.project.repository import git
from tests.support.harvest import PART, preserve_experiment
from tests.support.execution import create


def decision(repo, source, **overrides):
    return harvest.record(
        repo,
        source,
        **{
            "kind": "code",
            "parts": [PART],
            "purpose": "Preserve the API",
            "target": None,
            "target_commit": None,
            "decision": "defer",
            "verification": None,
            "note": "Not yet tested.\n",
            **overrides,
        },
    )


def test_catalog_roundtrips_registered_fields_and_ignores_integration_tables(
    repo, preserved_experiment
):
    catalog = repo / "experiments/INDEX.md"
    catalog.write_text(
        catalog.read_text()
        + "\n## Integration assessment\n\n| Field | Value |\n| --- | --- |\n| Title | A later assessment |\n\n```bash\ngit show unrelated:example\n```\n"
    )
    git(repo, "add", str(catalog))
    git(repo, "commit", "-m", "test: add knowledge assessment")
    (entry,) = catalog_entries(repo)
    assert entry["title"] == "Cached search | comparison"
    assert entry["limits"] == "Only small inputs; 日本語"
    assert entry["saved_commit"] == preserved_experiment["saved_commit"]
    source = read_experiment(repo, entry["id"])
    assert PART in source["changed_files"]
    assert (
        source["implementation_commit"]
        == preserved_experiment["result"]["implementation_commit"]
    )


def test_saved_results_work_in_a_clone_without_execution_history(
    repo, preserved_experiment, tmp_path
):
    clone = tmp_path / "cloned"
    subprocess.run(
        ["git", "clone", "--no-local", str(repo), str(clone)],
        check=True,
        capture_output=True,
    )
    assert not (clone / ".mutagi/runs").exists()
    source = read_experiment(clone, preserved_experiment["id"])
    assert source["saved_commit"] == preserved_experiment["saved_commit"]
    assert "Synthetic" in harvest.prepare(
        clone, source, purpose="Try it", kind="code", parts=[PART], target=None
    )


def test_inherited_source_retains_parent_and_fixed_revisions(
    repo, preserved_experiment
):
    child = preserve_experiment(repo, parent=preserved_experiment)
    source = read_experiment(repo, child["id"])
    assert source["parent_ids"] == [preserved_experiment["id"]]
    assert source["base_commit"] == preserved_experiment["saved_commit"]


def test_configured_catalog_and_experiment_paths_are_used(repo):
    (repo / "experiments").rename(repo / "research trials")
    config = repo / ".mutagi/config.toml"
    config.write_text(
        config.read_text()
        .replace('"experiments"', '"research trials"')
        .replace('"experiments/INDEX.md"', '"research trials/INDEX.md"')
    )
    git(repo, "add", ".")
    git(repo, "commit", "-m", "test: configure a catalog path with spaces")
    state = preserve_experiment(repo)
    source = read_experiment(repo, state["id"])
    assert source["record"].startswith("research trials/")
    prompt = harvest.prepare(
        repo,
        source,
        purpose="Review an idea",
        kind="idea",
        parts=[source["record"] + "#Human harvesting"],
        target=None,
    )
    assert source["record"] in prompt
    assert "## Human harvesting" in prompt


def test_unavailable_saved_objects_explain_what_needs_fetching(
    repo, preserved_experiment
):
    path = repo / "experiments/INDEX.md"
    path.write_text(
        path.read_text().replace(preserved_experiment["saved_commit"], "a" * 40)
    )
    git(repo, "add", str(path))
    git(repo, "commit", "-m", "test: reference an unavailable result")
    with pytest.raises(
        ValueError, match="fetch the recorded exploration's Git objects"
    ):
        read_experiment(repo, preserved_experiment["id"])


@pytest.mark.parametrize("status", ["observed", "refuted", "unresolved", "blocked"])
def test_all_reported_outcomes_remain_available(repo, status):
    state = preserve_experiment(repo, status=status)
    assert read_experiment(repo, state["id"])["status"] == status


@pytest.mark.parametrize("reason", ["preparation_failed", "time_limit"])
def test_recovery_records_remain_available_without_an_observed_implementation(
    repo, tmp_path, reason
):
    job = create(repo, tmp_path)
    job.prepare()
    job.state["reason"] = reason
    job.collect()
    job.preserve()
    job.register()
    source = read_experiment(repo, job.state["id"])
    assert source["implementation_commit"] is None
    assert source["status"] == (
        "blocked" if reason == "preparation_failed" else "unresolved"
    )
    prompt = harvest.prepare(
        repo,
        source,
        purpose="Inspect the remaining evidence",
        kind="idea",
        parts=[],
        target=None,
    )
    assert "Not recorded; the observed implementation is unverified." in prompt


@pytest.mark.parametrize(
    "defect", ["id", "duplicate", "body", "implementation", "parents"]
)
def test_saved_metadata_is_checked_without_repairing_evidence(
    repo, preserved_experiment, defect
):
    state = preserved_experiment
    worktree = Path(state["worktree"])
    path = worktree / state["result"]["record"]
    original = path.read_text()
    changed = {
        "id": original.replace("id: " + state["id"], "id: " + str(uuid4()), 1),
        "duplicate": original.replace(
            "id: " + state["id"], ("id: " + state["id"] + "\n") * 2, 1
        ),
        "body": "# Metadata example\n\n" + original,
        "implementation": original.replace(
            "implementation_commit: " + state["result"]["implementation_commit"],
            "implementation_commit: " + "a" * 40,
            1,
        ),
        "parents": original.replace(
            "parent_ids: []", "parent_ids: [" + str(uuid4()) + "]", 1
        ),
    }[defect]
    path.write_text(changed)
    git(worktree, "add", str(path))
    git(worktree, "commit", "-m", "test: malformed metadata")
    invalid = git(worktree, "rev-parse", "HEAD")
    catalog = repo / "experiments/INDEX.md"
    catalog.write_text(catalog.read_text().replace(state["saved_commit"], invalid))
    git(repo, "add", str(catalog))
    git(repo, "commit", "-m", "test: point at malformed record")
    with pytest.raises((ValueError, subprocess.CalledProcessError)):
        read_experiment(repo, state["id"])
    assert path.read_text() == changed


def test_duplicate_catalog_ids_are_rejected(repo, preserved_experiment):
    path = repo / "experiments/INDEX.md"
    content = path.read_text()
    path.write_text(content + content[content.index("<!-- forest-run:") :])
    git(repo, "add", str(path))
    git(repo, "commit", "-m", "test: duplicate registration")
    with pytest.raises(ValueError, match="duplicate experiment ID"):
        catalog_entries(repo)


def test_checkout_uses_saved_revision_and_retains_dirty_trials(
    repo, preserved_experiment
):
    source = read_experiment(repo, preserved_experiment["id"])
    shared = git(repo, "rev-parse", "HEAD")
    result = harvest.checkout(repo, source)
    path = Path(result["path"])
    assert git(path, "rev-parse", "HEAD") == source["saved_commit"]
    assert not git(path, "branch", "--show-current")
    assert not result["dirty"]
    (path / PART).write_text("local trial changes\n")
    assert harvest.checkout(repo, source)["dirty"]
    assert (path / PART).read_text() == "local trial changes\n"
    assert git(repo, "rev-parse", "HEAD") == shared
    assert not git(repo, "status", "--porcelain")


def test_checkout_rejects_moved_trial_without_reset(repo, preserved_experiment):
    source = read_experiment(repo, preserved_experiment["id"])
    path = Path(harvest.checkout(repo, source)["path"])
    git(path, "checkout", "--detach", source["base_commit"])
    with pytest.raises(ValueError, match="contents were retained"):
        harvest.checkout(repo, source)
    assert git(path, "rev-parse", "HEAD") == source["base_commit"]


def test_checkout_rejects_symlinks(repo, preserved_experiment, tmp_path):
    source = read_experiment(repo, preserved_experiment["id"])
    path = repo / ".mutagi/worktrees" / ("harvest-" + source["id"])
    path.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="symbolic link"):
        harvest.checkout(repo, source)


@pytest.mark.parametrize("kind", harvest.KINDS)
def test_prepare_embeds_fixed_sources_without_touching_target(
    repo, preserved_experiment, tmp_path, kind
):
    source = read_experiment(repo, preserved_experiment["id"])
    destination = tmp_path / "destination"
    destination.mkdir()
    content = harvest.prepare(
        repo,
        source,
        purpose="Keep the existing API. 日本語",
        kind=kind,
        parts=[PART],
        target=destination,
    )
    assert source["saved_commit"] in content
    assert source["implementation_commit"] in content
    assert "def search(items):" in content
    assert "Keep the existing API. 日本語" in content
    assert "This prompt generation has not reproduced" in content
    assert "git -C" in content and source["record"] in content
    assert list(destination.iterdir()) == []
    assert not git(repo, "status", "--porcelain")


def test_selects_exact_markdown_section_ignoring_fenced_headings(
    repo, preserved_experiment
):
    source = read_experiment(repo, preserved_experiment["id"])
    # The fixture's real observation record has multiple sections and fenced examples.
    parts = harvest.selected_parts(
        repo, source, [source["record"] + "#Human harvesting"]
    )
    assert parts[0]["text"].startswith("## Human harvesting")
    assert "## Starting conditions" not in parts[0]["text"]
    headings = harvest.markdown_headings(
        "# Real\n```python\n# False\n```\n## Next\n~~~\n# Hidden\n~~~\n"
    )
    assert [item["title"] for item in headings] == ["Real", "Next"]


@pytest.mark.parametrize(
    "part",
    [
        "../outside",
        ".git/config",
        "missing.txt",
        PART + "#Missing",
        PART + "#",
        "experiments",
    ],
)
def test_invalid_source_selections_fail(repo, preserved_experiment, part):
    source = read_experiment(repo, preserved_experiment["id"])
    with pytest.raises((ValueError, subprocess.CalledProcessError)):
        harvest.selected_parts(repo, source, [part])


def test_binary_selection_is_referenced_and_fences_preserve_source_text(
    repo, preserved_experiment
):
    source = read_experiment(repo, preserved_experiment["id"])
    path = str(Path(source["record"]).parent / "input.bin")
    prompt = harvest.prepare(
        repo,
        source,
        purpose="Reuse inputs",
        kind="counterexample",
        parts=[path],
        target=None,
    )
    assert "Binary file (7 bytes)" in prompt
    with pytest.raises(ValueError, match="binary file"):
        harvest.selected_parts(repo, source, [path + "#Heading"])
    assert harvest.quoted("```\nsource\n```").startswith("````text\n")
    assert "preserve spaces  \n\n\n" in harvest.quoted("preserve spaces  \n\n\n")


def test_records_are_versioned_separately_while_exploration_is_locked(
    repo, preserved_experiment
):
    source = read_experiment(repo, preserved_experiment["id"])
    shared = git(repo, "rev-parse", "HEAD")
    index = Path(git(repo, "rev-parse", "--absolute-git-dir")) / "index"
    original_index = index.read_bytes()
    with lock(repo):
        first = decision(repo, source)
        second = decision(
            repo,
            source,
            kind="idea",
            parts=[source["record"] + "#Changes in knowledge"],
            decision="adopt",
            note="Use only the idea.\n",
            verification="Read the source; destination tests not run.",
        )
    records = read_records(repo)
    assert {r["id"] for r in records} == {first["record"]["id"], second["record"]["id"]}
    assert first["record"]["verification"] is None
    assert second["record"]["decision"] == "adopt"
    assert git(repo, "rev-parse", BRANCH + "^") == first["commit"]
    assert git(repo, "rev-parse", "HEAD") == shared
    assert index.read_bytes() == original_index
    assert not git(repo, "status", "--porcelain")
    assert (
        "human_decision: unreviewed"
        in read_experiment(repo, source["id"])["record_text"]
    )


def test_destination_revision_is_explicit_and_verified(repo, preserved_experiment):
    source = read_experiment(repo, preserved_experiment["id"])
    with pytest.raises(ValueError, match="requires --target"):
        decision(repo, source, target_commit="HEAD")
    result = decision(repo, source, target=repo, target_commit="HEAD", decision="adopt")
    assert result["record"]["target_commit"] == git(repo, "rev-parse", "HEAD")
    assert result["record"]["verification"] is None


def test_record_preserves_staged_and_untracked_user_work(repo, preserved_experiment):
    source = read_experiment(repo, preserved_experiment["id"])
    instructions = repo / "AGENTS.md"
    instructions.write_text("# Unrelated staged user instructions\n")
    git(repo, "add", "AGENTS.md")
    scratch = repo / "scratch.txt"
    scratch.write_text("untracked user work\n")
    index = Path(git(repo, "rev-parse", "--absolute-git-dir")) / "index"
    original_index = index.read_bytes()
    staged = git(repo, "diff", "--cached")
    shared = git(repo, "rev-parse", "HEAD")
    decision(repo, source)
    assert index.read_bytes() == original_index
    assert git(repo, "diff", "--cached") == staged
    assert instructions.read_text() == "# Unrelated staged user instructions\n"
    assert scratch.read_text() == "untracked user work\n"
    assert git(repo, "rev-parse", "HEAD") == shared


def test_record_rejects_empty_note_and_invalid_source_parts(repo, preserved_experiment):
    source = read_experiment(repo, preserved_experiment["id"])
    with pytest.raises(ValueError, match="nonempty decision note"):
        decision(repo, source, note="\n")
    with pytest.raises(ValueError, match="regular file"):
        decision(repo, source, parts=["nonexistent"])
    assert read_records(repo) == []


def test_record_refuses_to_change_a_checked_out_notes_branch(
    repo, preserved_experiment, tmp_path
):
    source = read_experiment(repo, preserved_experiment["id"])
    first = decision(repo, source)
    notes = tmp_path / "notes"
    git(repo, "worktree", "add", str(notes), BRANCH)
    with pytest.raises(ValueError, match="unchecked out"):
        decision(repo, source)
    assert git(repo, "rev-parse", REF) == first["commit"]


def test_malformed_harvesting_notes_are_reported(
    repo, preserved_experiment, monkeypatch
):
    source = read_experiment(repo, preserved_experiment["id"])
    result = decision(repo, source)
    import mutagi.execution.harvest_records as store

    original = store.git

    def corrupted(root, *args):
        if args[:1] == ("show",):
            value = dict(
                result["record"], source={**result["record"]["source"], "id": None}
            )
            return json.dumps(value)
        return original(root, *args)

    monkeypatch.setattr(store, "git", corrupted)
    with pytest.raises(ValueError, match="source ID must be a UUID string"):
        read_records(repo)


def test_concurrent_record_change_is_not_overwritten(
    repo, preserved_experiment, monkeypatch
):
    source = read_experiment(repo, preserved_experiment["id"])
    first = decision(repo, source)
    import mutagi.execution.harvest_records as store

    original = store.git
    competitor = {}

    def competing(root, *args):
        if args[:1] == ("update-ref",):
            monkeypatch.setattr(store, "git", original)
            competitor.update(decision(repo, source, note="Concurrent decision.\n"))
        return original(root, *args)

    monkeypatch.setattr(store, "git", competing)
    with pytest.raises(RuntimeError, match="changed concurrently"):
        decision(repo, source)
    assert git(repo, "rev-parse", REF) == competitor["commit"]
    assert {r["id"] for r in read_records(repo)} == {
        first["record"]["id"],
        competitor["record"]["id"],
    }
