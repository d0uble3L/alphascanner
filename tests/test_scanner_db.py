import pytest

from alphascanner.db import connect
from alphascanner.scanner import FilterParams, historical_avg_volume, latest_snapshot_df, scan


def _insert_snapshot(conn, coin_id, fetched_at, total_volume):
    conn.execute(
        "INSERT INTO snapshots (coin_id, symbol, name, total_volume, fetched_at) "
        "VALUES (?,?,?,?,?)",
        (coin_id, coin_id, coin_id.title(), total_volume, fetched_at),
    )


def test_latest_snapshot_df_empty_without_data(db_path):
    df, latest = latest_snapshot_df()
    assert df.empty
    assert latest is None


def test_latest_snapshot_df_returns_most_recent(db_path):
    with connect(db_path) as conn:
        _insert_snapshot(conn, "btc", "2024-01-01T00:00:00", 10)
        _insert_snapshot(conn, "btc", "2024-01-02T00:00:00", 20)
    df, latest = latest_snapshot_df()
    assert latest == "2024-01-02T00:00:00"
    assert list(df["total_volume"]) == [20]


def test_historical_avg_volume_excludes_latest_snapshot(db_path):
    with connect(db_path) as conn:
        _insert_snapshot(conn, "btc", "2024-01-01T00:00:00", 10)
        _insert_snapshot(conn, "btc", "2024-01-02T00:00:00", 30)
        _insert_snapshot(conn, "btc", "2024-01-03T00:00:00", 999)  # latest, excluded
    avg = historical_avg_volume()
    row = avg[avg["coin_id"] == "btc"].iloc[0]
    assert row["avg_volume"] == pytest.approx(20.0)


def test_historical_avg_volume_needs_at_least_two_snapshots(db_path):
    with connect(db_path) as conn:
        _insert_snapshot(conn, "btc", "2024-01-01T00:00:00", 10)
    avg = historical_avg_volume()
    assert avg.empty


def test_scan_end_to_end_uses_real_db(db_path):
    with connect(db_path) as conn:
        _insert_snapshot(conn, "btc", "2024-01-01T00:00:00", 10)
        _insert_snapshot(conn, "btc", "2024-01-02T00:00:00", 40)
    df, fetched_at = scan(FilterParams(sort_by="volume"))
    assert fetched_at == "2024-01-02T00:00:00"
    assert list(df["coin_id"]) == ["btc"]


def test_scan_empty_db_returns_empty(db_path):
    df, fetched_at = scan(FilterParams())
    assert df.empty
    assert fetched_at is None
