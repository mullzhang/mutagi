from __future__ import annotations

import pytest

from mutagi.project.config import parse_project
from tests.support.repositories import PROJECT_CONFIG


@pytest.mark.parametrize(
    "replacement",
    ["references = [42]", 'references = "README.md"', 'references = ["../outside"]'],
)
def test_invalid_configuration_identifies_file_and_field(replacement):
    text = PROJECT_CONFIG.read_text()
    text = text.replace('references = ["AGENTS.md", "docs/operations.md"]', replacement)
    with pytest.raises(ValueError, match=r"\.mutagi/config.toml") as caught:
        parse_project(text)
    assert "references" in str(caught.value)
    with pytest.raises(ValueError, match="missing required field"):
        parse_project('name = "incomplete"')


@pytest.mark.parametrize("path", ["../outside", "/tmp/outside", ".git/config"])
def test_rejects_paths_outside_project(path):
    # Given an explicit experiment path outside the authorized project.
    text = PROJECT_CONFIG.read_text().replace(
        'experiments = "experiments"', f'experiments = "{path}"'
    )
    # When validated, then reject it before file operations.
    with pytest.raises(ValueError, match="project-relative path"):
        parse_project(text)
