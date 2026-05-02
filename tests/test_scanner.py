import pandas as pd
import pytest

from alphascanner.scanner import FilterParams, apply_filters, with_volume_surge


@pytest.fixture
def latest_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "coin_id": "btc",
                "symbol": "btc",
                "name": "Bitcoin",
                "current_price": 60000,
                "market_cap": 1_200_000_000_000,
                "total_volume": 30_000_000_000,
                "price_change_pct_1h": 0.5,
                "price_change_pct_24h": 2.0,
                "price_change_pct_7d": 5.0,
                "ath": 70000,
                "ath_change_pct": -14.3,
            },
            {
                "coin_id": "moon",
                "symbol": "moon",
                "name": "Moon Coin",
                "current_price": 0.05,
                "market_cap": 50_000_000,
                "total_volume": 10_000_000,
                "price_change_pct_1h": 8.0,
                "price_change_pct_24h": 25.0,
                "price_change_pct_7d": 80.0,
                "ath": 0.052,
                "ath_change_pct": -3.8,
            },
            {
                "coin_id": "stable",
                "symbol": "usdc",
                "name": "USD Coin",
                "current_price": 1.0,
                "market_cap": 30_000_000_000,
                "total_volume": 5_000_000_000,
                "price_change_pct_1h": 0.01,
                "price_change_pct_24h": 0.0,
                "price_change_pct_7d": 0.02,
                "ath": 1.17,
                "ath_change_pct": -14.5,
            },
        ]
    )


@pytest.fixture
def avg_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"coin_id": "btc", "avg_volume": 25_000_000_000},
            {"coin_id": "moon", "avg_volume": 2_000_000},
            {"coin_id": "stable", "avg_volume": 5_000_000_000},
        ]
    )


def test_volume_surge_computed(latest_df, avg_df):
    enriched = with_volume_surge(latest_df, avg_df)
    assert "volume_surge" in enriched.columns
    moon = enriched[enriched["coin_id"] == "moon"].iloc[0]
    assert moon["volume_surge"] == pytest.approx(5.0)


def test_volume_surge_no_history(latest_df):
    enriched = with_volume_surge(latest_df, pd.DataFrame(columns=["coin_id", "avg_volume"]))
    assert enriched["volume_surge"].isna().all()


def test_market_cap_filter(latest_df, avg_df):
    enriched = with_volume_surge(latest_df, avg_df)
    out = apply_filters(enriched, FilterParams(max_market_cap=100_000_000, sort_by="volume"))
    assert list(out["coin_id"]) == ["moon"]


def test_volume_surge_sort_and_filter(latest_df, avg_df):
    enriched = with_volume_surge(latest_df, avg_df)
    out = apply_filters(
        enriched,
        FilterParams(min_volume_surge=2.0, sort_by="volume_surge", limit=10),
    )
    assert out.iloc[0]["coin_id"] == "moon"
    assert "stable" not in list(out["coin_id"])


def test_pct_change_filter(latest_df, avg_df):
    enriched = with_volume_surge(latest_df, avg_df)
    out = apply_filters(
        enriched,
        FilterParams(min_pct_change_24h=10, sort_by="pct_change_24h"),
    )
    assert list(out["coin_id"]) == ["moon"]


def test_near_ath_filter(latest_df, avg_df):
    enriched = with_volume_surge(latest_df, avg_df)
    out = apply_filters(
        enriched,
        FilterParams(near_ath_pct=0.95, sort_by="market_cap"),
    )
    # within 5% of ATH → ath_change_pct >= -5
    assert list(out["coin_id"]) == ["moon"]


def test_limit_truncates(latest_df, avg_df):
    enriched = with_volume_surge(latest_df, avg_df)
    out = apply_filters(enriched, FilterParams(sort_by="market_cap", limit=2))
    assert len(out) == 2


def test_unknown_sort_falls_back(latest_df, avg_df):
    enriched = with_volume_surge(latest_df, avg_df)
    out = apply_filters(enriched, FilterParams(sort_by="not_a_column"))
    assert not out.empty


def test_empty_input():
    out = apply_filters(pd.DataFrame(), FilterParams())
    assert out.empty
