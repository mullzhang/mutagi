from __future__ import annotations

import copy

import pytest

import mutagi.execution.round as round_controller
import mutagi.execution.series as series
from tests.support.codex import prepare_round_codex
from tests.support.plans import LIMITS, PROPOSAL, round_plan


@pytest.fixture
def round_environment(repo, fake_codex, tmp_path):
    calls = prepare_round_codex(repo, fake_codex, tmp_path)
    return repo, round_plan(), calls


@pytest.fixture
def round_case(round_environment):
    repo, plan, calls = round_environment
    return repo, round_controller.create_round(repo, plan), calls


@pytest.fixture
def adaptive_case(round_environment, fake_codex):
    repo, plan, calls = round_environment
    tasks = plan.pop("tasks")
    plan.update(
        seconds=900,
        round_seconds=600,
        rounds=[{"tasks": tasks}, {"tasks": copy.deepcopy(tasks)}],
        adaptation=LIMITS,
    )
    with fake_codex.open("a") as stream:
        stream.write(
            "\nif s.get('adaptation'):\n value=json.loads(out.read_text());value['next_round']="
            + repr(PROPOSAL)
            + ";out.write_text(json.dumps(value))\n"
        )

    return repo, series.create_series(repo, plan), calls
