from dataclasses import dataclass

import pandas as pd

from .config import settings
from .db import connect

SORT_MAP = {
    "volume_surge": "volume_surge",
    "pct_change_1h": "price_change_pct_1h",
    "pct_change_24h": "price_change_pct_24h",
    "pct_change_7d": "price_change_pct_7d",
    "volume": "total_volume",
    "market_cap": "market_cap",
}


@dataclass
class FilterParams:
    sort_by: str = "volume_surge"
    limit: int = 20
    min_market_cap: float | None = None
    max_market_cap: float | None = None
    min_volume: float | None = None
    min_pct_change_1h: float | None = None
    min_pct_change_24h: float | None = None
    min_pct_change_7d: float | None = None
    min_volume_surge: float | None = None
    near_ath_pct: float | None = None


def latest_snapshot_df() -> tuple[pd.DataFrame, str | None]:
    with connect() as conn:
        row = conn.execute("SELECT MAX(fetched_at) FROM snapshots").fetchone()
        latest = row[0] if row else None
        if not latest:
            return pd.DataFrame(), None
        df = pd.read_sql_query(
            "SELECT * FROM snapshots WHERE fetched_at = ?",
            conn,
            params=(latest,),
        )
    return df, latest


def historical_avg_volume() -> pd.DataFrame:
    """Average volume per coin across the most recent N snapshots, excluding the newest."""
    with connect() as conn:
        ts_rows = conn.execute(
            "SELECT DISTINCT fetched_at FROM snapshots ORDER BY fetched_at DESC LIMIT ?",
            (settings.history_window + 1,),
        ).fetchall()
        timestamps = [r[0] for r in ts_rows]
        if len(timestamps) < 2:
            return pd.DataFrame(columns=["coin_id", "avg_volume"])
        history = timestamps[1:]
        placeholders = ",".join("?" for _ in history)
        df = pd.read_sql_query(
            f"SELECT coin_id, AVG(total_volume) AS avg_volume FROM snapshots "
            f"WHERE fetched_at IN ({placeholders}) GROUP BY coin_id",
            conn,
            params=history,
        )
    return df


def with_volume_surge(latest: pd.DataFrame, avg: pd.DataFrame) -> pd.DataFrame:
    if latest.empty:
        return latest
    if avg.empty:
        latest = latest.copy()
        latest["avg_volume"] = pd.NA
        latest["volume_surge"] = pd.NA
        return latest
    merged = latest.merge(avg, on="coin_id", how="left")
    merged["volume_surge"] = merged["total_volume"] / merged["avg_volume"]
    return merged


def apply_filters(df: pd.DataFrame, params: FilterParams) -> pd.DataFrame:
    if df.empty:
        return df
    out = df
    if params.min_market_cap is not None:
        out = out[out["market_cap"] >= params.min_market_cap]
    if params.max_market_cap is not None:
        out = out[out["market_cap"] <= params.max_market_cap]
    if params.min_volume is not None:
        out = out[out["total_volume"] >= params.min_volume]
    if params.min_pct_change_1h is not None:
        out = out[out["price_change_pct_1h"] >= params.min_pct_change_1h]
    if params.min_pct_change_24h is not None:
        out = out[out["price_change_pct_24h"] >= params.min_pct_change_24h]
    if params.min_pct_change_7d is not None:
        out = out[out["price_change_pct_7d"] >= params.min_pct_change_7d]
    if params.min_volume_surge is not None:
        out = out[out["volume_surge"] >= params.min_volume_surge]
    if params.near_ath_pct is not None:
        # ath_change_pct is negative; near_ath_pct=0.95 → within 5% of ATH → >= -5
        threshold = -100 * (1 - params.near_ath_pct)
        out = out[out["ath_change_pct"] >= threshold]

    sort_col = SORT_MAP.get(params.sort_by, "volume_surge")
    if sort_col not in out.columns:
        sort_col = "total_volume"
    out = out.sort_values(sort_col, ascending=False, na_position="last")
    return out.head(params.limit)


def scan(params: FilterParams) -> tuple[pd.DataFrame, str | None]:
    latest, fetched_at = latest_snapshot_df()
    if latest.empty:
        return latest, None
    enriched = with_volume_surge(latest, historical_avg_volume())
    return apply_filters(enriched, params), fetched_at
