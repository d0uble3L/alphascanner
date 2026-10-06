import asyncio
from pathlib import Path

import pytest

from alphascanner import scheduler
from alphascanner.config import settings


def test_heartbeat_lives_next_to_the_db(monkeypatch):
    monkeypatch.setattr(settings, "db_path", "/data/alphascanner.db")
    # Must match the path docker-compose.yml's scheduler healthcheck reads.
    assert scheduler.heartbeat_path() == Path("/data/scheduler.heartbeat")
    monkeypatch.setattr(settings, "db_path", "data/alphascanner.db")
    assert scheduler.heartbeat_path() == Path("data/scheduler.heartbeat")


def test_main_loop_survives_fetch_failure_and_writes_heartbeat(db_path, monkeypatch):
    heartbeat_path = Path(db_path).parent / "scheduler.heartbeat"

    call_count = {"n": 0}

    async def fake_run_fetch():
        call_count["n"] += 1
        raise RuntimeError("boom")

    async def fake_sleep(_):
        if call_count["n"] >= 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(scheduler, "run_fetch", fake_run_fetch)
    monkeypatch.setattr(scheduler.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(scheduler.main())

    # Two failed iterations were survived without the loop crashing outright.
    assert call_count["n"] == 2
    assert heartbeat_path.exists()


def test_touch_heartbeat_warns_on_oserror(monkeypatch, caplog):
    monkeypatch.setattr(settings, "db_path", "/no/such/directory/alphascanner.db")
    with caplog.at_level("WARNING"):
        scheduler._touch_heartbeat()
    assert any("Could not write heartbeat file" in r.message for r in caplog.records)
