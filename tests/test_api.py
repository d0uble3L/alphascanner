import pytest
from fastapi.testclient import TestClient

import alphascanner.api as api_module
from alphascanner.api import app
from alphascanner.config import settings
from alphascanner.db import connect


@pytest.fixture
def client(db_path):
    with TestClient(app) as c:
        yield c


def test_healthz(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_index_renders(client):
    resp = client.get("/")
    assert resp.status_code == 200


def test_api_screen_no_data(client):
    resp = client.get("/api/screen")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"fetched_at": None, "count": 0, "results": []}


def test_api_screen_with_data(client, db_path):
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO snapshots (coin_id, symbol, name, total_volume, fetched_at) "
            "VALUES (?,?,?,?,?)",
            ("btc", "btc", "Bitcoin", 100.0, "2024-01-01T00:00:00"),
        )
    resp = client.get("/api/screen", params={"sort_by": "volume"})
    body = resp.json()
    assert body["count"] == 1
    assert body["results"][0]["coin_id"] == "btc"


def test_screen_html_renders_rows(client, db_path):
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO snapshots (coin_id, symbol, name, total_volume, fetched_at) "
            "VALUES (?,?,?,?,?)",
            ("btc", "btc", "Bitcoin", 100.0, "2024-01-01T00:00:00"),
        )
    resp = client.get("/screen")
    assert resp.status_code == 200
    assert "Bitcoin" in resp.text


def test_screen_query_rejects_invalid_limit(client):
    resp = client.get("/api/screen", params={"limit": 0})
    assert resp.status_code == 422


def test_fetch_status_survives_db_error_and_logs(client, monkeypatch, caplog):
    def broken_connect(*a, **k):
        raise RuntimeError("db exploded")

    monkeypatch.setattr(api_module, "connect", broken_connect)
    with caplog.at_level("ERROR"):
        resp = client.get("/screen")
    assert resp.status_code == 200
    assert any("Failed to read fetch status" in r.message for r in caplog.records)


def test_no_auth_required_by_default(client):
    resp = client.get("/api/screen")
    assert resp.status_code == 200


def test_protected_routes_require_auth_when_password_set(client, monkeypatch):
    monkeypatch.setattr(settings, "auth_password", "secret")
    resp = client.get("/api/screen")
    assert resp.status_code == 401


def test_protected_routes_accept_correct_credentials(client, monkeypatch):
    monkeypatch.setattr(settings, "auth_password", "secret")
    resp = client.get("/api/screen", auth=("admin", "secret"))
    assert resp.status_code == 200


def test_protected_routes_reject_wrong_password(client, monkeypatch):
    monkeypatch.setattr(settings, "auth_password", "secret")
    resp = client.get("/api/screen", auth=("admin", "wrong"))
    assert resp.status_code == 401


def test_healthz_never_requires_auth(client, monkeypatch):
    monkeypatch.setattr(settings, "auth_password", "secret")
    resp = client.get("/healthz")
    assert resp.status_code == 200


def test_rate_limit_blocks_after_threshold(client, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_per_minute", 3)
    for _ in range(3):
        assert client.get("/api/screen").status_code == 200
    resp = client.get("/api/screen")
    assert resp.status_code == 429


def test_healthz_is_not_rate_limited(client, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_per_minute", 1)
    for _ in range(5):
        assert client.get("/healthz").status_code == 200
