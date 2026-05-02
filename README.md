# AlphaScanner

Altcoin opportunity screener. Pulls market data from CoinGecko on a 15-minute
schedule, stores snapshots in SQLite, and surfaces top movers via a CLI and a
FastAPI web UI. Filter by volume surge, price change windows, market-cap band,
and proximity to all-time high.

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

```bash
pip install -e ".[dev]"
cp .env.example .env
alphascanner init           # create the SQLite db
alphascanner fetch          # one-shot snapshot
alphascanner screen --sort-by volume_surge --min-volume-surge 2 --limit 20
```

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
```

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
free CoinGecko tier works without an API key; set
`ALPHASCANNER_COINGECKO_API_KEY` to use a Demo or Pro key.

## Tests

```bash
pytest
ruff check .
```

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
```
