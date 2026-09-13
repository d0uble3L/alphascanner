import asyncio

import httpx
import pytest

from alphascanner import fetcher
from alphascanner.config import settings
from alphascanner.db import connect


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_headers_empty_without_key(monkeypatch):
    monkeypatch.setattr(settings, "coingecko_api_key", None)
    assert fetcher._headers() == {}


def test_headers_use_demo_header_for_free_tier(monkeypatch):
    monkeypatch.setattr(settings, "coingecko_api_key", "abc123")
    monkeypatch.setattr(settings, "coingecko_base_url", "https://api.coingecko.com/api/v3")
    assert fetcher._headers() == {"x-cg-demo-api-key": "abc123"}


def test_headers_use_pro_header_for_pro_base_url(monkeypatch):
    monkeypatch.setattr(settings, "coingecko_api_key", "abc123")
    monkeypatch.setattr(settings, "coingecko_base_url", "https://pro-api.coingecko.com/api/v3")
    assert fetcher._headers() == {"x-cg-pro-api-key": "abc123"}


def test_row_handles_missing_optional_fields():
    coin = {"id": "btc", "symbol": "btc", "name": "Bitcoin"}
    row = fetcher._row(coin, "2024-01-01T00:00:00")
    assert row[0] == "btc"
    assert row[4] is None  # current_price missing -> None
    assert row[-1] == "2024-01-01T00:00:00"


def test_store_snapshot_persists_rows(db_path):
    coins = [
        {"id": "btc", "symbol": "btc", "name": "Bitcoin", "total_volume": 1.0},
        {"id": "eth", "symbol": "eth", "name": "Ethereum", "total_volume": 2.0},
    ]
    n, ts = fetcher.store_snapshot(coins)
    assert n == 2
    with connect(db_path) as conn:
        rows = conn.execute("SELECT coin_id FROM snapshots ORDER BY coin_id").fetchall()
    assert [r[0] for r in rows] == ["btc", "eth"]
    assert ts


def test_fetch_page_sends_expected_params(monkeypatch):
    captured = {}

    async def fake_get(self, url, params=None, headers=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        return _FakeResponse([{"id": "btc"}])

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    async def run():
        async with httpx.AsyncClient() as client:
            return await fetcher.fetch_page(client, 1)

    result = asyncio.run(run())
    assert result == [{"id": "btc"}]
    assert captured["params"]["page"] == 1


def test_fetch_all_paginates_without_real_sleep(monkeypatch):
    monkeypatch.setattr(settings, "fetch_pages", 2)
    calls = []

    async def fake_get(self, url, params=None, headers=None, timeout=None):
        calls.append(params["page"])
        return _FakeResponse([{"id": f"coin{params['page']}"}])

    async def fake_sleep(_):
        return None

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    monkeypatch.setattr(fetcher.asyncio, "sleep", fake_sleep)

    coins = asyncio.run(fetcher.fetch_all())
    assert calls == [1, 2]
    assert [c["id"] for c in coins] == ["coin1", "coin2"]


def test_run_fetch_success_logs_ok(db_path, monkeypatch):
    async def fake_fetch_all():
        return [{"id": "btc", "symbol": "btc", "name": "Bitcoin"}]

    monkeypatch.setattr(fetcher, "fetch_all", fake_fetch_all)
    n, ts = asyncio.run(fetcher.run_fetch())
    assert n == 1
    with connect(db_path) as conn:
        row = conn.execute("SELECT ok, message FROM fetch_log").fetchone()
    assert row["ok"] == 1
    assert ts


def test_run_fetch_http_error_logs_failure_and_reraises(db_path, monkeypatch):
    async def fake_fetch_all():
        request = httpx.Request("GET", "https://example.com")
        response = httpx.Response(500, request=request)
        raise httpx.HTTPStatusError("boom", request=request, response=response)

    monkeypatch.setattr(fetcher, "fetch_all", fake_fetch_all)
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(fetcher.run_fetch())
    with connect(db_path) as conn:
        row = conn.execute("SELECT ok, status_code FROM fetch_log").fetchone()
    assert row["ok"] == 0
    assert row["status_code"] == 500
