import re
from dataclasses import dataclass
from typing import Annotated, Literal

import pandas as pd
from pydantic import BaseModel, BeforeValidator, Field

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


_SortBy = Literal["volume_surge", "pct_change_1h", "pct_change_24h", "pct_change_7d", "volume", "market_cap"]

_NoneIfEmpty = BeforeValidator(lambda v: None if v == "" else v)
# ge/le constraints live inside the float branch so None bypasses them
_OptFloat = Annotated[float | None, _NoneIfEmpty]
_OptFloatPos = Annotated[Annotated[float, Field(ge=0)] | None, _NoneIfEmpty]
_OptAthPct = Annotated[Annotated[float, Field(ge=0.0, le=1.0)] | None, _NoneIfEmpty]


class ScreenQuery(BaseModel):
    """Validated FilterParams. The single source of truth for what a screen (or saved preset) may contain."""

    sort_by: _SortBy = "volume_surge"
    limit: Annotated[int, Field(ge=1, le=200)] = 20
    min_market_cap: _OptFloatPos = None
    max_market_cap: _OptFloatPos = None
    min_volume: _OptFloatPos = None
    min_pct_change_1h: _OptFloat = None
    min_pct_change_24h: _OptFloat = None
    min_pct_change_7d: _OptFloat = None
    min_volume_surge: _OptFloatPos = None
    near_ath_pct: _OptAthPct = None

    def to_filter_params(self) -> FilterParams:
        return FilterParams(**self.model_dump())


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
            f"SELECT coin_id, AVG(total_volume) AS avg_volume FROM snapshots "  # nosec B608
            f"WHERE fetched_at IN ({placeholders}) GROUP BY coin_id",
            conn,
            params=history,
        )
    return df


# CoinGecko coin ids are short slugs like "bitcoin" or "usd-coin".
COIN_ID_RE = re.compile(r"[A-Za-z0-9._-]{1,100}")


def coin_history(coin_id: str, limit: int = 200) -> pd.DataFrame:
    """One coin's snapshots, oldest first, each with its own volume_surge.

    Surge for a snapshot is its volume over the mean of the coin's previous
    `history_window` snapshots, so the newest row matches the main screen's
    surge whenever the coin is present in every snapshot.
    """
    if not COIN_ID_RE.fullmatch(coin_id):
        return pd.DataFrame()
    with connect() as conn:
        # Fetch extra rows so even the oldest returned row has a full baseline.
        df = pd.read_sql_query(
            "SELECT * FROM snapshots WHERE coin_id = ? ORDER BY fetched_at DESC LIMIT ?",
            conn,
            params=(coin_id, limit + settings.history_window),
        )
    if df.empty:
        return df
    df = df.iloc[::-1].reset_index(drop=True)
    baseline = df["total_volume"].shift(1).rolling(settings.history_window, min_periods=1).mean()
    df["avg_volume"] = baseline
    df["volume_surge"] = df["total_volume"] / baseline.replace(0, float("nan"))
    return df.tail(limit).reset_index(drop=True)


def with_volume_surge(latest: pd.DataFrame, avg: pd.DataFrame) -> pd.DataFrame:
    if latest.empty:
        return latest
    if avg.empty:
        latest = latest.copy()
        latest["avg_volume"] = pd.NA
        latest["volume_surge"] = pd.NA
        return latest
    merged = latest.merge(avg, on="coin_id", how="left")
    # A zero avg_volume would otherwise divide to +inf and falsely sort
    # to the top of a descending volume_surge ranking.
    safe_avg = merged["avg_volume"].replace(0, float("nan"))
    merged["volume_surge"] = merged["total_volume"] / safe_avg
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
