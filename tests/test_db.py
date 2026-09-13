from alphascanner.db import connect


def test_init_db_creates_tables(db_path):
    with connect(db_path) as conn:
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert {"snapshots", "fetch_log"}.issubset(tables)


def test_connect_enables_wal_mode(db_path):
    with connect(db_path) as conn:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"


def test_connect_sets_busy_timeout(db_path):
    with connect(db_path) as conn:
        timeout_ms = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    assert timeout_ms > 0


def test_connect_commits_on_success(db_path):
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO fetch_log (attempted_at, ok, status_code, message) VALUES (?,?,?,?)",
            ("2024-01-01T00:00:00", 1, None, "ok"),
        )
    with connect(db_path) as conn:
        row = conn.execute("SELECT COUNT(*) FROM fetch_log").fetchone()
    assert row[0] == 1


def test_connect_does_not_persist_on_exception(db_path):
    try:
        with connect(db_path) as conn:
            conn.execute(
                "INSERT INTO fetch_log (attempted_at, ok, status_code, message) VALUES (?,?,?,?)",
                ("2024-01-01T00:00:00", 1, None, "should not persist"),
            )
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    with connect(db_path) as conn:
        row = conn.execute("SELECT COUNT(*) FROM fetch_log").fetchone()
    assert row[0] == 0
