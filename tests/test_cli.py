from typer.testing import CliRunner

import alphascanner.cli as cli_module
from alphascanner.cli import app
from alphascanner.db import connect

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
