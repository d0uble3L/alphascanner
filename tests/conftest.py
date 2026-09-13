import pytest

from alphascanner.config import settings
from alphascanner.db import init_db


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "test.db"
    monkeypatch.setattr(settings, "db_path", str(path))
    init_db()
    return str(path)
