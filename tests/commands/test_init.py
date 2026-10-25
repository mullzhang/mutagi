from __future__ import annotations

import io
import sys

import pytest

import mutagi.commands.init as init_command
import mutagi.project.initialize as project_initialize
from mutagi.project.initialize import initialize
from mutagi.project.repository import project_at
from tests.support.repositories import snapshot


class Terminal(io.StringIO):
    def isatty(self):
        return True


@pytest.mark.parametrize("confirmation", ["yes", "no"])
def test_interactive_initialization_previews_and_respects_choice(
    empty_repo, monkeypatch, capsys, confirmation
):
    answers = iter(
        ["", "python-stdlib", "src/", "", "", "selected-model", "medium", confirmation]
    )
    before = snapshot(empty_repo)
    monkeypatch.setattr(sys, "stdin", Terminal())
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    monkeypatch.setattr(
        sys, "argv", ["mutagi init", "--repo", str(empty_repo), "--interactive"]
    )
    init_command.main()
    output = capsys.readouterr().out
    assert 'model = "selected-model"' in output
    assert "--- .mutagi/config.toml ---" in output
    if confirmation == "yes":
        assert (
            project_at(empty_repo)["preparation"]
            == project_initialize.PRESETS["python-stdlib"]["commands"]
        )
    else:
        assert snapshot(empty_repo) == before


def test_interactive_dry_run_and_eof_leave_no_files(empty_repo, monkeypatch):
    before = snapshot(empty_repo)
    monkeypatch.setattr(sys, "stdin", Terminal())
    answers = iter(["", "", "", "", "", ""])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    monkeypatch.setattr(
        sys,
        "argv",
        ["mutagi init", "--repo", str(empty_repo), "--interactive", "--dry-run"],
    )
    init_command.main()
    assert snapshot(empty_repo) == before

    def eof(prompt):
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)
    with pytest.raises(SystemExit):
        init_command.main()
    assert snapshot(empty_repo) == before


def test_interactive_rejects_nonterminal_input(empty_repo, monkeypatch):
    before = snapshot(empty_repo)
    monkeypatch.setattr(sys, "stdin", io.StringIO())
    monkeypatch.setattr(
        sys, "argv", ["mutagi init", "--repo", str(empty_repo), "--interactive"]
    )
    with pytest.raises(ValueError, match="requires a terminal"):
        init_command.main()
    assert snapshot(empty_repo) == before


def test_init_dry_run_and_explicit_paths_preserve_existing_files(empty_repo, capsys):
    (empty_repo / "README.md").write_text("# Input\n")
    ignore = empty_repo / ".gitignore"
    original = b"# Keep this comment\r\ncache/\r\n.mutagi/runs/"
    ignore.write_bytes(original)
    before = snapshot(empty_repo)
    preview = initialize(
        empty_repo,
        'A "quoted" project',
        ["lib/", "tests/"],
        ["README.md"],
        dry_run=True,
    )
    init_command.show_initialization(preview, dry_run=True)
    assert snapshot(empty_repo) == before
    assert ".mutagi/config.toml" in capsys.readouterr().out
    initialize(empty_repo, 'A "quoted" project', ["lib/", "tests/"], ["README.md"])
    config = project_at(empty_repo)
    assert config["name"] == 'A "quoted" project'
    assert config["exploration_paths"] == ["knowledge/notes/", "lib/", "tests/"]
    assert config["references"] == ["README.md"]
    assert ignore.read_bytes() == original + b"\n/.mutagi/worktrees/\n"
    before = snapshot(empty_repo)
    with pytest.raises(ValueError, match="mutagi check"):
        initialize(empty_repo, None, [], [])
    assert snapshot(empty_repo) == before
