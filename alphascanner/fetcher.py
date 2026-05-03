import asyncio
import logging
from datetime import datetime, timezone

import httpx

from .config import settings
from .db import connect

log = logging.getLogger(__name__)

INSERT_SQL = """
INSERT INTO snapshots (
    coin_id, symbol, name, image, current_price, market_cap, market_cap_rank,
    total_volume, high_24h, low_24h, price_change_pct_1h, price_change_pct_24h,
    price_change_pct_7d, ath, ath_change_pct, atl, fetched_at
) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
"""


def _headers() -> dict[str, str]:
    if settings.coingecko_api_key:
        return {"x-cg-demo-api-key": settings.coingecko_api_key}
    return {}


async def fetch_page(client: httpx.AsyncClient, page: int) -> list[dict]:
    params = {
        "vs_currency": settings.vs_currency,
        "order": "market_cap_desc",
        "per_page": settings.per_page,
        "page": page,
        "price_change_percentage": "1h,24h,7d",
        "sparkline": "false",
    }
    r = await client.get(
        f"{settings.coingecko_base_url}/coins/markets",
        params=params,
        headers=_headers(),
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


async def fetch_all() -> list[dict]:
    coins: list[dict] = []
    async with httpx.AsyncClient() as client:
        for page in range(1, settings.fetch_pages + 1):
            coins.extend(await fetch_page(client, page))
            if page < settings.fetch_pages:
                await asyncio.sleep(2)
    return coins


def _row(c: dict, fetched_at: str) -> tuple:
    return (
        c["id"],
        c["symbol"],
        c["name"],
        c.get("image"),
        c.get("current_price"),
        c.get("market_cap"),
        c.get("market_cap_rank"),
        c.get("total_volume"),
        c.get("high_24h"),
        c.get("low_24h"),
        c.get("price_change_percentage_1h_in_currency"),
        c.get("price_change_percentage_24h_in_currency"),
        c.get("price_change_percentage_7d_in_currency"),
        c.get("ath"),
        c.get("ath_change_percentage"),
        c.get("atl"),
        fetched_at,
    )


def store_snapshot(coins: list[dict]) -> tuple[int, str]:
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = [_row(c, fetched_at) for c in coins]
    with connect() as conn:
        conn.executemany(INSERT_SQL, rows)
    return len(rows), fetched_at


def _log_fetch(ok: bool, status_code: int | None, message: str) -> None:
    attempted_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect() as conn:
        conn.execute(
            "INSERT INTO fetch_log (attempted_at, ok, status_code, message) VALUES (?,?,?,?)",
            (attempted_at, int(ok), status_code, message),
        )


async def run_fetch() -> tuple[int, str]:
    try:
        coins = await fetch_all()
        n, ts = store_snapshot(coins)
        _log_fetch(ok=True, status_code=None, message=f"{n} coins stored")
        return n, ts
    except httpx.HTTPStatusError as exc:
        _log_fetch(ok=False, status_code=exc.response.status_code, message=str(exc))
        raise
    except Exception as exc:
        _log_fetch(ok=False, status_code=None, message=str(exc))
        raise
