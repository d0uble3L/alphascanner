import asyncio
import logging

from .config import settings
from .db import init_db
from .fetcher import run_fetch

log = logging.getLogger(__name__)


async def main() -> None:
    init_db()
    interval = settings.fetch_interval_minutes * 60
    log.info("Scheduler starting; interval = %ds", interval)
    while True:
        try:
            n, ts = await run_fetch()
            log.info("Stored %d coins at %s", n, ts)
        except Exception:
            log.exception("Fetch failed")
        await asyncio.sleep(interval)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(main())
