# AlphaScanner

AlphaScanner is a crypto market screener that shows you which altcoins are
showing unusual activity right now. It answers: **"Which coins are moving, and
why might they be worth watching?"**

It pulls the top 500 coins by market cap from CoinGecko every 15 minutes and
surfaces them through filters:

- **Volume surge** — coins trading at a multiple of their recent average volume (unusual buying/selling pressure)
- **Price change** — biggest movers over 1h, 24h, or 7d
- **Near all-time high** — coins within X% of their ATH
- **Market cap band** — filter by size (large cap, mid cap, small cap)

The goal is to spot coins breaking out or showing early momentum signals before
they become obvious — the kind of thing a trader would otherwise scan manually
across multiple tabs.

Pulls market data from CoinGecko on a 15-minute schedule, stores snapshots in
SQLite, and surfaces results via a CLI and a FastAPI web UI.

## Stack

- **Python 3.11+** — typed, async where it matters
- **SQLite** — snapshot history; no server to run
- **httpx** — async CoinGecko client (free tier; Pro key drops in)
- **pandas** — ranking and filter math
- **FastAPI + Jinja2 + HTMX** — server-rendered UI, no JS build step
- **Typer + Rich** — CLI
- **Docker / Compose** — `web` + `scheduler` containers, shared volume
- **pytest + ruff** — tests + lint

## Quick start (local)

Requires Python 3.11+ (3.12 is what CI and Docker use).

```bash
python3.12 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env
alphascanner init           # create the SQLite db
alphascanner fetch          # one-shot snapshot
alphascanner screen --sort-by volume_surge --min-volume-surge 2 --limit 20
```

Leave `ALPHASCANNER_COINGECKO_API_KEY` blank in `.env` to use the free
CoinGecko tier — no key needed. See [Configuration](#configuration) for
Demo/Pro key setup.

Run the web UI:

```bash
uvicorn alphascanner.api:app --reload
# open http://localhost:8000
```

Run the scheduler (15-minute snapshots) in another terminal:

```bash
python -m alphascanner.scheduler
```

## Quick start (Docker)

```bash
cp .env.example .env
docker compose up --build
# web on http://localhost:8000, scheduler writes to ./data
docker compose ps   # both services should reach "healthy" within ~30s
```

`web` is checked via `GET /healthz`; `scheduler` has no HTTP endpoint, so it's
checked via a heartbeat file (`/data/scheduler.heartbeat`) touched after every
fetch loop iteration — a wedged scheduler shows as `unhealthy` within an hour.

## Signals

| Signal              | How it's computed                                    |
| ------------------- | ---------------------------------------------------- |
| Volume surge        | `current_24h_volume / mean(prev N snapshot volumes)` |
| Price breakout      | CoinGecko `price_change_percentage_{1h,24h,7d}`      |
| Near all-time high  | `ath_change_percentage >= -5` (≈ within 5% of ATH)   |
| Market-cap band     | `min_market_cap` / `max_market_cap` filters          |

`N` is `ALPHASCANNER_HISTORY_WINDOW` (default 20 ≈ 5 hours at 15-min cadence).
On a fresh DB, volume surge is `null` until at least two snapshots exist.

## CLI

```
alphascanner init                   # create db
alphascanner fetch                  # one snapshot from CoinGecko
alphascanner screen [OPTIONS]       # rank latest snapshot
```

`screen` flags: `--sort-by`, `--limit`, `--min-market-cap`, `--max-market-cap`,
`--min-volume`, `--min-pct-change-{1h,24h,7d}`, `--min-volume-surge`,
`--near-ath-pct`.

## API

- `GET /` — HTMX dashboard
- `GET /screen` — HTML table fragment (HTMX target)
- `GET /api/screen` — JSON, same query params as the CLI
- `GET /healthz`

## Configuration

All settings are env vars prefixed `ALPHASCANNER_` (see `.env.example`). The
free CoinGecko tier works without an API key. To use a Demo key, just set
`ALPHASCANNER_COINGECKO_API_KEY`. To use a Pro key, also set
`ALPHASCANNER_COINGECKO_BASE_URL=https://pro-api.coingecko.com/api/v3` — the
client picks the matching auth header (`x-cg-demo-api-key` /
`x-cg-pro-api-key`) from whichever base URL is configured.

## Tests

```bash
pytest                                              # 44 tests: pure logic + db/api/cli/scheduler
ruff check .                                        # lint
bandit -r alphascanner/ -ll -x alphascanner/templates  # security static analysis
```

These are the same checks CI runs (`.github/workflows/ci.yml`), plus a
`pip-audit` dependency scan.

## Layout

```
alphascanner/
  config.py     # pydantic-settings
  db.py         # sqlite schema + connection
  fetcher.py    # CoinGecko client + insert
  scanner.py    # ranking / filter logic (pure pandas)
  cli.py        # Typer
  api.py        # FastAPI
  scheduler.py  # async loop
  templates/
tests/
Dockerfile
docker-compose.yml
requirements-lock.txt  # pinned runtime deps used by the Docker build
```
