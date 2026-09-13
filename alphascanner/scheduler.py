import asyncio
import logging
import os
from pathlib import Path

from .config import settings
from .db import init_db
from .fetcher import run_fetch

log = logging.getLogger(__name__)

HEARTBEAT_PATH = os.environ.get("ALPHASCANNER_HEARTBEAT_PATH", "/data/scheduler.heartbeat")


def _touch_heartbeat() -> None:
    try:
        Path(HEARTBEAT_PATH).touch()
    except OSError:
        log.warning("Could not write heartbeat file %s", HEARTBEAT_PATH)


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
        _touch_heartbeat()
        await asyncio.sleep(interval)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(main())
