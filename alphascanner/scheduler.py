import asyncio
import logging
from pathlib import Path

from .alerts import check_alerts
from .config import settings
from .db import init_db
from .fetcher import run_fetch

log = logging.getLogger(__name__)

HEARTBEAT_FILENAME = "scheduler.heartbeat"


def heartbeat_path() -> Path:
    # Lives next to the DB: /data/scheduler.heartbeat in Docker (what the
    # docker-compose healthcheck reads), ./data/scheduler.heartbeat locally.
    return Path(settings.db_path).parent / HEARTBEAT_FILENAME


def _touch_heartbeat() -> None:
    path = heartbeat_path()
    try:
        path.touch()
    except OSError:
        log.warning("Could not write heartbeat file %s", path)


async def main() -> None:
    init_db()
    interval = settings.fetch_interval_minutes * 60
    log.info("Scheduler starting; interval = %ds", interval)
    _touch_heartbeat()
    while True:
        try:
            n, ts = await run_fetch()
            log.info("Stored %d coins at %s", n, ts)
        except Exception:
            log.exception("Fetch failed")
        else:
            try:
                for r in await asyncio.to_thread(check_alerts):
                    if r.delivered:
                        log.info("Alert %r: notified %d new match(es)", r.preset_name, r.new)
            except Exception:
                log.exception("Alert check failed")
        _touch_heartbeat()
        await asyncio.sleep(interval)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(main())
