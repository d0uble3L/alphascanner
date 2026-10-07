import math

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from alphascanner.api import _sparkline, app
from alphascanner.cli import app as cli_app
from alphascanner.config import settings
from alphascanner.db import connect
from alphascanner.scanner import FilterParams, coin_history, scan

SNAPSHOTS = ["2026-01-01T00:00:00", "2026-01-01T00:15:00", "2026-01-01T00:30:00"]


def _seed(db_path, volumes_by_coin):
    with connect(db_path) as conn:
        for coin_id, volumes in volumes_by_coin.items():
            for ts, vol in zip(SNAPSHOTS, volumes, strict=False):
                conn.execute(
                    "INSERT INTO snapshots (coin_id, symbol, name, current_price, total_volume, "
                    "market_cap, fetched_at) VALUES (?,?,?,?,?,?,?)",
                    (coin_id, coin_id[:3], coin_id.title(), vol / 10, vol, vol * 100, ts),
                )


@pytest.fixture
def client(db_path):
    with TestClient(app) as c:
        yield c


def test_history_is_oldest_first_with_per_snapshot_surge(db_path):
    _seed(db_path, {"bitcoin": [100.0, 300.0, 400.0]})
    df = coin_history("bitcoin")
    assert list(df["fetched_at"]) == SNAPSHOTS
    assert math.isnan(df["volume_surge"].iloc[0])  # no prior snapshot
    assert df["volume_surge"].iloc[1] == pytest.approx(3.0)  # 300 / 100
    assert df["volume_surge"].iloc[2] == pytest.approx(2.0)  # 400 / mean(100, 300)


def test_newest_surge_matches_main_screen(db_path):
    _seed(db_path, {"bitcoin": [100.0, 300.0, 400.0], "ethereum": [50.0, 50.0, 200.0]})
    screened, _ = scan(FilterParams(sort_by="volume_surge"))
    for coin_id in ["bitcoin", "ethereum"]:
        expected = screened.loc[screened["coin_id"] == coin_id, "volume_surge"].iloc[0]
        assert coin_history(coin_id)["volume_surge"].iloc[-1] == pytest.approx(expected)


def test_surge_matches_main_screen_for_a_coin_missing_from_snapshots(db_path, monkeypatch):
    monkeypatch.setattr(settings, "history_window", 1)
    _seed(db_path, {"bitcoin": [100.0, 300.0, 400.0]})
    with connect(db_path) as conn:  # ethereum is absent from the middle snapshot
        for ts, vol in [(SNAPSHOTS[0], 50.0), (SNAPSHOTS[2], 200.0)]:
            conn.execute(
                "INSERT INTO snapshots (coin_id, symbol, name, total_volume, fetched_at) "
                "VALUES ('ethereum', 'eth', 'Ethereum', ?, ?)",
                (vol, ts),
            )
    screened, _ = scan(FilterParams(sort_by="volume_surge"))
    main = screened.loc[screened["coin_id"] == "ethereum", "volume_surge"].iloc[0]
    # The main screen's 1-snapshot window is the middle snapshot, which ethereum
    # isn't in, so it has no baseline; history must agree rather than use t0.
    assert math.isnan(main)
    assert math.isnan(coin_history("ethereum")["volume_surge"].iloc[-1])


def test_history_window_limits_the_baseline(db_path, monkeypatch):
    monkeypatch.setattr(settings, "history_window", 1)
    _seed(db_path, {"bitcoin": [100.0, 300.0, 600.0]})
    assert coin_history("bitcoin")["volume_surge"].iloc[-1] == pytest.approx(2.0)  # 600 / 300


def test_limit_keeps_newest_rows_with_full_baseline(db_path):
    _seed(db_path, {"bitcoin": [100.0, 300.0, 400.0]})
    df = coin_history("bitcoin", limit=1)
    assert list(df["fetched_at"]) == [SNAPSHOTS[-1]]
    assert df["volume_surge"].iloc[0] == pytest.approx(2.0)


def test_zero_baseline_is_nan_not_inf(db_path):
    _seed(db_path, {"dead": [0.0, 50.0]})
    assert math.isnan(coin_history("dead")["volume_surge"].iloc[-1])


@pytest.mark.parametrize("bad", ["", "a/b", "x' OR '1'='1", "x" * 101])
def test_invalid_coin_id_never_reaches_the_database(monkeypatch, bad):
    import alphascanner.scanner as scanner_module

    def no_db(*a, **k):
        raise AssertionError("database was queried with an invalid coin id")

    monkeypatch.setattr(scanner_module, "connect", no_db)
    assert coin_history(bad).empty


def test_sparkline():
    assert _sparkline([1.0]) is None
    assert _sparkline([None, float("nan"), 2.0]) is None
    points = _sparkline([1.0, None, 3.0], width=100, height=10)
    # x spans the full width; skipped values keep their x position
    assert points.split() == ["0.0,6.0", "100.0,4.0"]
    flat = _sparkline([5.0, 5.0], width=100, height=10)
    assert flat is not None


def test_history_api(client, db_path):
    _seed(db_path, {"bitcoin": [100.0, 300.0, 400.0]})
    body = client.get("/api/coins/bitcoin/history").json()
    assert body["coin_id"] == "bitcoin"
    assert body["count"] == 3
    assert body["results"][0]["volume_surge"] is None  # NaN serialized as null
    assert body["results"][-1]["volume_surge"] == pytest.approx(2.0)
    assert client.get("/api/coins/bitcoin/history", params={"limit": 2}).json()["count"] == 2
    assert client.get("/api/coins/bitcoin/history", params={"limit": 0}).status_code == 422


def test_history_unknown_coin_is_404(client):
    assert client.get("/api/coins/nope/history").status_code == 404
    assert client.get("/coin/nope").status_code == 404


def test_coin_page_renders_charts_and_rows(client, db_path):
    _seed(db_path, {"bitcoin": [100.0, 300.0, 400.0]})
    resp = client.get("/coin/bitcoin")
    assert resp.status_code == 200
    assert "Bitcoin" in resp.text
    assert resp.text.count("<polyline") == 3
    assert "3 snapshots" in resp.text
    assert SNAPSHOTS[-1] in resp.text


def test_coin_page_with_one_snapshot_has_no_charts(client, db_path):
    _seed(db_path, {"bitcoin": [100.0]})
    resp = client.get("/coin/bitcoin")
    assert resp.status_code == 200
    assert "<polyline" not in resp.text
    assert "Not enough data to chart yet" in resp.text


def test_screen_rows_link_to_coin_page(client, db_path):
    _seed(db_path, {"bitcoin": [100.0]})
    assert 'href="/coin/bitcoin"' in client.get("/screen").text


def test_history_routes_require_auth_when_password_set(client, db_path, monkeypatch):
    _seed(db_path, {"bitcoin": [100.0]})
    monkeypatch.setattr(settings, "auth_password", "secret")
    assert client.get("/coin/bitcoin").status_code == 401
    assert client.get("/api/coins/bitcoin/history").status_code == 401
    assert client.get("/coin/bitcoin", auth=("admin", "secret")).status_code == 200


def test_history_cli(db_path):
    _seed(db_path, {"bitcoin": [100.0, 300.0, 400.0]})
    runner = CliRunner()
    result = runner.invoke(cli_app, ["history", "bitcoin", "--limit", "2"])
    assert result.exit_code == 0
    assert "Bitcoin" in result.stdout
    assert SNAPSHOTS[-1] in result.stdout
    assert SNAPSHOTS[0] not in result.stdout

    missing = runner.invoke(cli_app, ["history", "nope"])
    assert missing.exit_code == 1
    assert "No snapshots for 'nope'" in missing.stdout
