import pytest

from tests.support.codex import create_codex
from tests.support.harvest import preserve_experiment
from tests.support.repositories import create_empty_repository, create_repository


@pytest.fixture
def repo(tmp_path):
    return create_repository(tmp_path)


@pytest.fixture
def empty_repo(tmp_path):
    return create_empty_repository(tmp_path)


@pytest.fixture
def fake_codex(tmp_path, monkeypatch):
    return create_codex(tmp_path, monkeypatch)


@pytest.fixture
def preserved_experiment(repo):
    return preserve_experiment(repo)
