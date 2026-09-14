import pytest

from alphascanner.config import settings
from alphascanner.db import init_db


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "test.db"
    monkeypatch.setattr(settings, "db_path", str(path))
    init_db()
    return str(path)


@pytest.fixture(autouse=True)
def _clean_web_state(monkeypatch):
    """Auth off and a clear rate-limit bucket by default for every test."""
    monkeypatch.setattr(settings, "auth_password", None)
    import alphascanner.api as api_module

    api_module._rate_limiter.reset()
    yield
    api_module._rate_limiter.reset()
