import pytest

from app import app


@pytest.fixture(autouse=True)
def isolate_runtime_session_config(monkeypatch):
    """Tests that exercise local login explicitly opt in with their own key."""
    monkeypatch.setitem(app.config, "SECRET_KEY", None)
