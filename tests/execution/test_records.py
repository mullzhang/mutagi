"""Assigned record identity must survive prompt generation and preservation."""

import json
from uuid import uuid4

import pytest

from mutagi.execution.experiment import Runner
from mutagi.project.repository import git
from tests.support.execution import create


@pytest.mark.parametrize("inherited", [False, True])
def test_prompt_supplies_fixed_record_metadata_and_registers_it(
    repo, tmp_path, fake_codex, inherited
):
    options = {}
    if inherited:
        parent = str(uuid4())
        (repo / "experiments/parent.md").write_text(f"---\nid: {parent}\n---\n")
        git(repo, "add", "experiments/parent.md")
        git(repo, "commit", "-m", "test: preserve parent record")
        options = {"parent_id": parent, "parent_record": "experiments/parent.md"}
    job = create(repo, tmp_path, **options)

    prompt = job.prompt()
    _, header, _ = prompt.rsplit("\n---\n", 2)
    fields = dict(line.split(": ", 1) for line in header.splitlines())
    expected = {
        "id": job.state["id"],
        "started_at": f"'{job.state['started_at']}'",
        "mode": "inherited" if inherited else "independent",
        "parent_ids": json.dumps(job.state["parent_ids"]),
        "base_commit": job.state["base"],
        "human_decision": "unreviewed",
    }
    assert fields == expected | {
        "implementation_commit": "null", "status": "unresolved"
    }
    assert Runner(job.run).prompt() == prompt
    if inherited:
        assert fields["id"] != parent

    result = job.advance()
    assert result["reason"] == "completed"
    assert result["result"]["status"] == "observed"
    record = git(repo, "show", result["saved_commit"] + ":" + result["result"]["record"])
    header = record.split("---", 2)[1].strip()
    saved = dict(line.split(": ", 1) for line in header.splitlines())
    assert {key: saved[key] for key in expected} == expected


@pytest.mark.parametrize("correct_id_in_body", [False, True])
def test_different_valid_uuid_is_rejected_and_preserved(
    repo, tmp_path, fake_codex, correct_id_in_body
):
    job = create(repo, tmp_path)
    job.prepare()
    job.execute()
    record = job.directory() / "README.md"
    wrong_id = "b1d687b4-8912-484f-9efc-7e58041f6c2f"
    assigned_id = job.state["id"]
    content = record.read_text().replace(f"id: {assigned_id}\n", f"id: {wrong_id}\n", 1)
    if correct_id_in_body:
        content += f"\nExample metadata in the body:\n\n```yaml\nid: {assigned_id}\n```\n"
    record.write_text(content)

    result = job.advance()

    assert result["reason"] == "invalid_result"
    assert result["result"]["status"] == "unresolved"
    assert "id" in result["result_error"]
    assert assigned_id in result["result_error"]
    assert wrong_id in result["result_error"]
    assert record.read_text() == content
    saved_path = result["saved_commit"] + ":" + record.relative_to(job.worktree).as_posix()
    assert git(repo, "show", saved_path) == content.strip()


@pytest.mark.parametrize("field", ["id", "parent_ids"])
def test_duplicate_identity_fields_are_rejected(repo, tmp_path, fake_codex, field):
    job = create(repo, tmp_path)
    job.prepare()
    job.execute()
    record = job.directory() / "README.md"
    content = record.read_text()
    original = next(line for line in content.splitlines() if line.startswith(field + ": "))
    record.write_text(content.replace(original, original + "\n" + original, 1))

    job.collect()

    assert job.state["reason"] == "invalid_result"
    assert f"exactly one {field} field" in job.state["result_error"]


def test_metadata_outside_frontmatter_does_not_validate(repo, tmp_path, fake_codex):
    job = create(repo, tmp_path)
    job.prepare()
    job.execute()
    record = job.directory() / "README.md"
    record.write_text("# Metadata example\n\n" + record.read_text())

    job.collect()

    assert job.state["reason"] == "invalid_result"
    assert "frontmatter" in job.state["result_error"]


def test_inheritance_checks_id_in_parent_frontmatter(repo, tmp_path):
    parent = str(uuid4())
    wrong_id = str(uuid4())
    record = "experiments/parent.md"
    (repo / record).write_text(
        f"---\nid: {wrong_id}\n---\n\n# Parent\n\n```yaml\nid: {parent}\n```\n"
    )
    git(repo, "add", record)
    git(repo, "commit", "-m", "test: preserve inconsistent parent metadata")

    with pytest.raises(ValueError, match="parent record.*does not match its ID"):
        create(repo, tmp_path, parent_id=parent, parent_record=record)

    assert not list((repo / ".mutagi/runs").glob("*/state.json"))
