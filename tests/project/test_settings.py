from __future__ import annotations

import pytest

from mutagi.project.config import parse_project
from tests.support.repositories import PROJECT_CONFIG


@pytest.mark.parametrize(
    "defaults",
    [
        'execution = "model"',
        '[execution]\nmodel = " "',
        '[execution]\neffort = "guess"',
        '[execution]\nunknown = "x"',
        "[execution.round]\nseconds = nan",
        "[execution.experiment]\nseconds = true",
        "[execution.experiment]\nround_seconds = 60",
        "[execution.series]\nrounds = 2",
    ],
)
def test_invalid_execution_defaults_are_actionable(defaults):
    base = PROJECT_CONFIG.read_text()
    text = (
        defaults + "\n" + base
        if defaults.startswith("execution =")
        else base + "\n" + defaults
    )
    with pytest.raises(ValueError, match="execution"):
        parse_project(text)
