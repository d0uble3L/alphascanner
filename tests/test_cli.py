from typer.testing import CliRunner

import alphascanner.cli as cli_module
from alphascanner.cli import app
from alphascanner.db import connect
from alphascanner.presets import get_preset

runner = CliRunner()


def test_init_command(db_path):
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0
    assert "initialized" in result.stdout.lower()


def test_screen_no_data_exits_nonzero(db_path):
    result = runner.invoke(app, ["screen"])
    assert result.exit_code == 1
    assert "no data yet" in result.stdout.lower()


def test_screen_with_data(db_path):
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO snapshots (coin_id, symbol, name, total_volume, fetched_at) "
            "VALUES (?,?,?,?,?)",
            ("btc", "btc", "Bitcoin", 100.0, "2024-01-01T00:00:00"),
        )
    result = runner.invoke(app, ["screen", "--sort-by", "volume"])
    assert result.exit_code == 0
    assert "btc" in result.stdout.lower()


def test_fetch_command_success(db_path, monkeypatch):
    async def fake_run_fetch():
        return 2, "2024-01-01T00:00:00"

    monkeypatch.setattr(cli_module, "run_fetch", fake_run_fetch)
    result = runner.invoke(app, ["fetch"])
    assert result.exit_code == 0
    assert "Stored 2 coins" in result.stdout


def test_fetch_command_reports_clean_error_instead_of_traceback(db_path, monkeypatch):
    async def fake_run_fetch():
        raise RuntimeError("network exploded")

    monkeypatch.setattr(cli_module, "run_fetch", fake_run_fetch)
    result = runner.invoke(app, ["fetch"])
    assert result.exit_code == 1
    assert "network exploded" in result.stdout
    assert "Traceback" not in result.stdout


def _insert_coins(db_path, *coins):
    with connect(db_path) as conn:
        for coin_id, volume in coins:
            conn.execute(
                "INSERT INTO snapshots (coin_id, symbol, name, total_volume, fetched_at) "
                "VALUES (?,?,?,?,?)",
                (coin_id, coin_id, coin_id.title(), volume, "2024-01-01T00:00:00"),
            )


def test_screen_save_as_then_run_preset(db_path):
    _insert_coins(db_path, ("btc", 200.0), ("eth", 100.0))

    saved = runner.invoke(app, ["screen", "--sort-by", "volume", "--limit", "1", "--save-as", "top1"])
    assert saved.exit_code == 0
    assert "Saved preset 'top1'" in saved.stdout

    # --limit 50 is ignored: the preset's own limit of 1 wins.
    result = runner.invoke(app, ["screen", "--preset", "top1", "--limit", "50"])
    assert result.exit_code == 0
    assert "btc" in result.stdout.lower()
    assert "eth" not in result.stdout.lower()
    assert "preset top1" in result.stdout


def test_save_as_works_before_any_data_exists(db_path):
    result = runner.invoke(app, ["screen", "--save-as", "early"])
    assert "Saved preset 'early'" in result.stdout
    assert result.exit_code == 1  # still reports "no data yet" for the screen itself


def test_screen_unknown_preset_exits_nonzero(db_path):
    result = runner.invoke(app, ["screen", "--preset", "missing"])
    assert result.exit_code == 1
    assert "No preset named 'missing'" in result.stdout


def test_save_as_rejects_invalid_name(db_path):
    result = runner.invoke(app, ["screen", "--save-as", "bad name"])
    assert result.exit_code == 1
    assert "Preset names must be" in result.stdout


def test_save_as_rejects_filters_the_api_would_reject(db_path):
    result = runner.invoke(app, ["screen", "--near-ath-pct", "5", "--save-as", "p"])
    assert result.exit_code == 1
    assert "preset not saved" in result.stdout
    assert get_preset("p") is None


def test_preset_list_and_delete(db_path):
    empty = runner.invoke(app, ["preset", "list"])
    assert empty.exit_code == 0
    assert "No presets saved yet" in empty.stdout

    runner.invoke(app, ["screen", "--min-volume-surge", "2", "--save-as", "surge2"])
    listed = runner.invoke(app, ["preset", "list"])
    assert "surge2" in listed.stdout
    assert "min_volume_surge=2.0" in listed.stdout

    deleted = runner.invoke(app, ["preset", "delete", "surge2"])
    assert deleted.exit_code == 0
    missing = runner.invoke(app, ["preset", "delete", "surge2"])
    assert missing.exit_code == 1
    assert "No preset named 'surge2'" in missing.stdout
