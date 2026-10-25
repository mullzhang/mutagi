import pytest

from tests.support.codex import create_diagnostic_codex


@pytest.fixture
def diagnostic_codex(tmp_path, monkeypatch):
    return create_diagnostic_codex(tmp_path, monkeypatch)
