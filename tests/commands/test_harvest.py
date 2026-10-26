from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from mutagi.commands import harvest as harvest_command
from tests.support.cli import invoke as invoke_cli
from mutagi.project.repository import git
from tests.support.harvest import PART, preserve_experiment
from tests.support.repositories import snapshot


def invoke(monkeypatch, repo, *arguments):
    monkeypatch.setattr(
        sys, "argv", ["mutagi harvest", "--repo", str(repo), *arguments]
    )
    invoke_cli(harvest_command)


def test_list_searches_findings_and_limits_without_requiring_a_model(
    repo, preserved_experiment, monkeypatch, capsys
):
    before = snapshot(repo)
    invoke(monkeypatch, repo, "list", "--search", "CACHED", "--json")
    (entry,) = json.loads(capsys.readouterr().out)
    assert entry["id"] == preserved_experiment["id"]
    assert entry["title"] == "Cached search | comparison"
    for query in ("repeated requests", "日本語"):
        invoke(monkeypatch, repo, "list", "--search", query)
        output = capsys.readouterr().out
        assert preserved_experiment["id"][:8] in output
        assert preserved_experiment["id"] not in output
        assert "mutagi harvest --repo" in output
    invoke(monkeypatch, repo, "list", "--search", "unmatched")
    assert "No registered results match" in capsys.readouterr().out
    assert snapshot(repo) == before


def test_list_includes_refuted_results_and_empty_catalogs(repo, monkeypatch, capsys):
    invoke(monkeypatch, repo, "list", "--json")
    assert json.loads(capsys.readouterr().out) == []
    state = preserve_experiment(repo, status="refuted")
    invoke(monkeypatch, repo, "list")
    output = capsys.readouterr().out
    assert state["id"][:8] in output and "refuted" in output


def test_show_reads_saved_record_when_the_shared_tree_has_other_changes(
    repo, preserved_experiment, monkeypatch, capsys
):
    catalog = repo / "experiments/INDEX.md"
    catalog.write_text("Uncommitted catalog edits, left alone.\n")
    (repo / "local.txt").write_text("Unrelated user work.\n")
    before = snapshot(repo)
    invoke(monkeypatch, repo, "show", preserved_experiment["id"], "--json")
    source = json.loads(capsys.readouterr().out)
    assert source["saved_commit"] == preserved_experiment["saved_commit"]
    assert source["record_text"].startswith("---\nid: " + preserved_experiment["id"])
    assert source["harvesting_records"] == []
    invoke(monkeypatch, repo, "show", preserved_experiment["id"])
    output = capsys.readouterr().out
    assert "## Reproduction and preservation" not in output
    assert "Findings excerpt" in output and "Limits excerpt" in output
    assert "mutagi harvest --repo" in output and "checkout" in output
    invoke(monkeypatch, repo, "show", preserved_experiment["id"][:8], "--record")
    assert capsys.readouterr().out == source["record_text"]
    invoke(monkeypatch, repo, "show", preserved_experiment["id"][:8], "--details")
    output = capsys.readouterr().out
    assert "Supporting evidence and references" in output
    assert "Record path" in output and "Synthetic fixture" in output
    assert output.index("Next") < output.index("Supporting evidence")
    assert snapshot(repo) == before


def test_unmarked_historical_references_are_reported_without_polluting_json(
    repo, preserved_experiment, monkeypatch, capsys
):
    path = repo / "experiments/INDEX.md"
    path.write_text(
        "```bash\ngit show aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa:experiments/older/README.md\n```\n\n"
        + path.read_text()
    )
    git(repo, "add", str(path))
    git(repo, "commit", "-m", "test: retain a historical saved-record reference")
    invoke(monkeypatch, repo, "list", "--json")
    output = capsys.readouterr()
    (entry,) = json.loads(output.out)
    assert entry["id"] == preserved_experiment["id"]
    assert entry["catalog_has_unmarked_references"]
    assert "unmarked saved-record references" in output.err


def test_show_preserves_existing_free_form_catalog_decisions(
    repo, preserved_experiment, monkeypatch, capsys
):
    path = repo / "experiments/INDEX.md"
    content = path.read_text()
    start = content.index("| Human decision | ")
    end = content.index("\n", start)
    path.write_text(
        content[:start]
        + "| Human decision | Adopted one idea; see design review. |"
        + content[end:]
    )
    git(repo, "add", str(path))
    git(repo, "commit", "-m", "test: retain an existing manual decision")
    invoke(monkeypatch, repo, "show", preserved_experiment["id"])
    output = capsys.readouterr().out
    assert "Catalog decision excerpt" in output
    assert "Adopted one idea; see design review." in output
    assert "Scoped harvesting notes" in output
    assert "human adoption are unverified" not in output


def test_prepare_stdout_is_a_prompt_and_makes_no_state_or_destination_changes(
    repo, preserved_experiment, monkeypatch, capsys, tmp_path
):
    destination = tmp_path / "destination"
    destination.mkdir()
    before = snapshot(repo)
    invoke(
        monkeypatch,
        repo,
        "prepare",
        preserved_experiment["id"],
        "--purpose",
        "Use a chosen result. 日本語",
        "--part",
        PART,
        "--target",
        str(destination),
    )
    prompt = capsys.readouterr().out
    assert prompt.startswith("Use a preserved exploration result")
    assert "Use a chosen result. 日本語" in prompt
    assert "def search(items):" in prompt
    assert list(destination.iterdir()) == []
    assert snapshot(repo) == before


def test_prepare_creates_utf8_output_equal_to_stdout_and_never_overwrites(
    repo, preserved_experiment, monkeypatch, capsys, tmp_path
):
    args = (
        "prepare",
        preserved_experiment["id"],
        "--purpose",
        "日本語の指示",
        "--kind",
        "idea",
        "--part",
        preserved_experiment["result"]["record"] + "#Human harvesting",
    )
    invoke(monkeypatch, repo, *args)
    prompt = capsys.readouterr().out
    output = tmp_path / "prompt.md"
    invoke(monkeypatch, repo, *args, "--output", str(output))
    assert output.read_text(encoding="utf-8") == prompt
    assert "Prompt saved" in capsys.readouterr().out
    with pytest.raises(SystemExit) as error:
        invoke(monkeypatch, repo, *args, "--output", str(output))
    assert error.value.code == 1
    assert "File exists" in capsys.readouterr().err
    assert output.read_text() == prompt


def test_record_uses_experiment_id_and_preserves_exact_note(
    repo, preserved_experiment, monkeypatch, capsys, tmp_path
):
    note = tmp_path / "review.md"
    content = "Adopt only the idea.\r\n利用先では未検証。\r\n"
    note.write_bytes(content.encode())
    shared = git(repo, "rev-parse", "HEAD")
    invoke(
        monkeypatch,
        repo,
        "record",
        preserved_experiment["id"],
        "--decision",
        "adopt",
        "--kind",
        "idea",
        "--part",
        preserved_experiment["result"]["record"] + "#Human harvesting",
        "--note-file",
        str(note),
        "--json",
    )
    saved = json.loads(capsys.readouterr().out)
    assert saved["record"]["note"] == content
    assert saved["record"]["source"]["id"] == preserved_experiment["id"]
    assert saved["record"]["verification"] is None
    invoke(monkeypatch, repo, "show", preserved_experiment["id"], "--json")
    assert json.loads(capsys.readouterr().out)["harvesting_records"] == [
        saved["record"]
    ]
    invoke(monkeypatch, repo, "show", preserved_experiment["id"], "--details")
    human = capsys.readouterr().out
    assert "adopt (idea)" in human
    assert "Verification reported" in human and "Not recorded" in human
    assert "利用先では未検証。" in human
    invoke(monkeypatch, repo, "list", "--json")
    assert json.loads(capsys.readouterr().out)[0]["harvesting_records"] == [
        saved["record"]
    ]
    assert git(repo, "rev-parse", "HEAD") == shared
    assert not git(repo, "status", "--porcelain")


def test_checkout_cli_reports_a_detached_trial(
    repo, preserved_experiment, monkeypatch, capsys
):
    invoke(monkeypatch, repo, "checkout", preserved_experiment["id"], "--json")
    result = json.loads(capsys.readouterr().out)
    assert Path(result["record"]).is_file()
    assert not result["dirty"]
    invoke(monkeypatch, repo, "checkout", preserved_experiment["id"])
    assert "No dependencies or tests were run" in capsys.readouterr().out


@pytest.mark.parametrize("action", ["list", "show", "checkout", "prepare", "record"])
def test_public_cli_help_and_errors_are_handled(tmp_path, action):
    command = [sys.executable, "-m", "mutagi", "harvest", action]
    result = subprocess.run(
        [*command, "--help"], cwd=tmp_path, capture_output=True, text=True
    )
    assert result.returncode == 0 and f"mutagi harvest {action}" in result.stdout
    invalid = subprocess.run(
        [sys.executable, "-m", "mutagi", "harvest", "show", "bad-id"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert invalid.returncode == 2 and "Traceback" not in invalid.stderr
