import pytest

from alphascanner.presets import (
    InvalidPresetName,
    delete_preset,
    get_preset,
    list_presets,
    save_preset,
)
from alphascanner.scanner import FilterParams, ScreenQuery


def test_save_and_get_round_trip(db_path):
    query = ScreenQuery(sort_by="volume", limit=5, min_volume_surge=2.0)
    save_preset("surge2", query)
    preset = get_preset("surge2")
    assert preset is not None
    assert preset.query == query


def test_save_overwrites_params_but_keeps_created_at(db_path):
    save_preset("p", ScreenQuery(limit=5))
    first = get_preset("p")
    save_preset("p", ScreenQuery(limit=50))
    second = get_preset("p")
    assert second.query.limit == 50
    assert second.created_at == first.created_at
    assert len(list_presets()) == 1


def test_list_is_sorted_by_name(db_path):
    for name in ["zeta", "alpha", "mid"]:
        save_preset(name, ScreenQuery())
    assert [p.name for p in list_presets()] == ["alpha", "mid", "zeta"]


def test_get_unknown_returns_none(db_path):
    assert get_preset("nope") is None


def test_delete(db_path):
    save_preset("p", ScreenQuery())
    assert delete_preset("p") is True
    assert get_preset("p") is None
    assert delete_preset("p") is False


@pytest.mark.parametrize("bad", ["", "has space", "a/b", "x" * 65, "<script>", "naïve"])
def test_invalid_names_rejected(db_path, bad):
    with pytest.raises(InvalidPresetName):
        save_preset(bad, ScreenQuery())


def test_to_filter_params_maps_every_field():
    query = ScreenQuery(
        sort_by="market_cap",
        limit=7,
        min_market_cap=1,
        max_market_cap=2,
        min_volume=3,
        min_pct_change_1h=4,
        min_pct_change_24h=5,
        min_pct_change_7d=6,
        min_volume_surge=7,
        near_ath_pct=0.9,
    )
    assert query.to_filter_params() == FilterParams(**query.model_dump())
    assert query.to_filter_params().near_ath_pct == 0.9
