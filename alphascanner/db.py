import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    coin_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    name TEXT NOT NULL,
    image TEXT,
    current_price REAL,
    market_cap REAL,
    market_cap_rank INTEGER,
    total_volume REAL,
    high_24h REAL,
    low_24h REAL,
    price_change_pct_1h REAL,
    price_change_pct_24h REAL,
    price_change_pct_7d REAL,
    ath REAL,
    ath_change_pct REAL,
    atl REAL,
    fetched_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_snapshots_coin_fetched
    ON snapshots(coin_id, fetched_at);
CREATE INDEX IF NOT EXISTS idx_snapshots_fetched
    ON snapshots(fetched_at);
"""


def init_db(db_path: str | None = None) -> None:
    path = db_path or settings.db_path
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as conn:
        conn.executescript(SCHEMA)


@contextmanager
def connect(db_path: str | None = None):
    path = db_path or settings.db_path
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()
