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

For a step-by-step tour of every feature (coin history, presets, alerts in
the CLI and web UI), see [Getting started](#getting-started-try-every-feature-locally).

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
checked via a heartbeat file touched after every fetch loop iteration — a
wedged scheduler shows as `unhealthy` within an hour. The file sits next to the
database (`/data/scheduler.heartbeat` in Docker, `./data/scheduler.heartbeat`
for local runs).

## Getting started: try every feature locally

A walkthrough from a fresh clone to each feature: the dashboard, coin
history, presets, and alerts (CLI, web UI, failing deliveries, and auth).
Run commands from the repo root.

### 1. Set up

```bash
git clone https://github.com/d0uble3L/alphascanner.git
cd alphascanner
python3.12 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env
```

Then edit `.env` (the database defaults to `./data/alphascanner.db`, so there's
no path to set):

```ini
ALPHASCANNER_FETCH_PAGES=2                       # gentler on CoinGecko's free tier
ALPHASCANNER_ALERTS_ALLOW_PRIVATE_WEBHOOKS=true  # only so alerts can reach the local test receiver
```

The last setting exists only for this walkthrough. Set it back to `false`
before the server is reachable by anyone else.

### 2. Load some data

```bash
alphascanner init
alphascanner fetch
# wait about a minute, then:
alphascanner fetch      # volume surge needs at least 2 snapshots
```

If CoinGecko returns a 429, wait a minute and try again.

### 3. Start the app

```bash
# Terminal 1: web UI
uvicorn alphascanner.api:app --reload      # open http://localhost:8000

# Terminal 2 (optional): fetches every 15 minutes and checks alerts after each fetch
python -m alphascanner.scheduler
```

With no password set, uvicorn logs `ALPHASCANNER_AUTH_PASSWORD is not set…`
at startup. That's expected; see [Auth](#auth) below.

### 4. Coin history

- Click any symbol or name in the dashboard to open `/coin/<id>`: price,
  volume, and volume-surge sparklines plus the snapshot table.
- From the CLI: `alphascanner history bitcoin --limit 10`

### 5. Alerts from the CLI

Start the test receiver in terminal 3. It prints every alert it gets:

```bash
python scripts/hook_receiver.py            # listens on http://127.0.0.1:9000
```

A new alert starts from the coins already matching, so it only notifies about
coins that match *later*. To see a notification straight away, create the alert
on a strict preset, then loosen the preset (the alert is kept):

```bash
alphascanner screen --min-volume 5000000000 --save-as demo   # strict: very few coins
alphascanner alert set demo http://127.0.0.1:9000/hook
alphascanner alert check     # 'demo': no new matches (N matching).

alphascanner screen --min-volume 50000000 --save-as demo     # loosen it
alphascanner alert check     # 'demo': notified K new match(es) ...
```

Terminal 3 prints `AlphaScanner: K new matches for preset 'demo': ...` with
each coin. Run `alert check` again and it reports no new matches: each coin is
sent once until it drops out of the preset and comes back.

### 6. Alerts in the web UI

1. Reload <http://localhost:8000> and expand **Alerts** under the preset bar.
   `demo` is listed with its webhook masked to the host.
2. To set an alert from the UI, pick a preset in the dropdown, paste
   `http://127.0.0.1:9000/x`, and click **Set alert**.
3. To check validation, try `ftp://x`, or click **Set alert** with no preset
   selected. The error shows inline.
4. To see a failing alert, start `python -m http.server 9001` in another
   terminal. It answers POSTs with HTTP 501, so deliveries to it fail:

   ```bash
   alphascanner screen --min-volume 5000000000 --save-as broken
   alphascanner alert set broken http://127.0.0.1:9001/
   alphascanner screen --min-volume 50000000 --save-as broken
   alphascanner alert check     # delivery failed: Webhook returned HTTP 501 (exit code 1)
   ```

   Within a minute (or straight away if you reload), the panel opens on its
   own. The header reads "… 1 failing", a red banner appears, and the error
   column shows `Webhook returned HTTP 501`. If you collapse the panel, it
   stays collapsed until another alert starts failing. The coins are retried
   on the next check.
5. Click **Delete** on a row, or delete the preset from the preset bar (that
   also deletes its alert). The panel updates without a reload.

### 7. A real Slack or public webhook (optional)

Set `ALPHASCANNER_ALERTS_ALLOW_PRIVATE_WEBHOOKS=false`, restart uvicorn, and
use a Slack incoming-webhook URL or one from <https://webhook.site>. The
payload's `text` field renders in Slack as is; the UI and
`alphascanner alert list` show only the host. A `http://localhost/...` URL is
now rejected as non-public.

### Auth

Set `ALPHASCANNER_AUTH_PASSWORD=something` in `.env` and restart uvicorn. The
startup warning goes away and the browser asks for a login (username `admin`,
or `ALPHASCANNER_AUTH_USERNAME`).

### Run the checks

See [Tests](#tests): `pytest`, `ruff check .`, and `bandit`.

With Docker (`docker compose up --build`) leave `ALPHASCANNER_DB_PATH` unset;
the image points it at the `/data` volume. A webhook receiver running on your
machine isn't `127.0.0.1` from inside the container, so use your host's address
for it instead.

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
alphascanner preset list            # show saved presets
alphascanner preset delete NAME     # remove a saved preset
alphascanner history COIN_ID        # one coin's snapshot history
alphascanner alert set PRESET URL   # webhook alert when coins start matching a preset
alphascanner alert list             # alerts and their last check/notification/error
alphascanner alert check            # check alerts now (the scheduler does this every fetch)
alphascanner alert delete PRESET    # remove an alert (keeps the preset)
```

`screen` flags: `--sort-by`, `--limit`, `--min-market-cap`, `--max-market-cap`,
`--min-volume`, `--min-pct-change-{1h,24h,7d}`, `--min-volume-surge`,
`--near-ath-pct`, plus `--save-as NAME` and `--preset NAME`.

### Presets

Save a set of filters under a name instead of retyping the flags:

```bash
alphascanner screen --min-volume-surge 2 --max-market-cap 500000000 --save-as small-cap-surge
alphascanner screen --preset small-cap-surge     # rerun it later
```

`--preset` runs the saved filters as-is; any other filter flags are ignored.
Names are 1–64 characters (letters, digits, `-`, `_`). Saving an existing name
overwrites it. Presets are validated the same way as API requests, so anything
saved from the CLI also works in the web UI and API, and vice versa.

In the web UI, the **Preset** bar above the filters loads a saved preset into
the form, saves the current filters (leave the name blank to overwrite the
selected preset), or deletes one.

### Alerts

Attach a webhook to a saved preset and get notified when coins *start*
matching it, instead of keeping the dashboard open:

```bash
alphascanner screen --min-volume-surge 3 --max-market-cap 500000000 --save-as surge3
alphascanner alert set surge3 https://hooks.slack.com/services/T000/B000/XXXX
```

Or in the web UI: open **Alerts** under the preset bar, select the preset,
paste the webhook URL, and click **Set alert**. The panel lists every alert
with its masked webhook, last check, last notification, and last error, and
has a Delete button for each. It refreshes every minute. When an alert is
failing, the panel header shows a red count, and the panel opens on its own
when a new failure appears, so you see the problem without expanding it.

The scheduler checks every alert after each successful fetch. It POSTs only
coins that weren't matching on the previous check, so a coin that stays in
the screen notifies once; if it drops out and comes back, it notifies again.
A new alert starts from whatever matches when you create it, so you hear
about coins that start matching *after* that, not a dump of everything at
once. "Matching" means passing the preset's filters; the preset's `limit`
only caps how many coins one notification lists, so coins trading places in
the ranking don't trigger anything.
If a delivery fails (non-2xx response, timeout), the error shows in
`alphascanner alert list` and the same coins are retried on the next check.
Running `alphascanner alert check` while the scheduler is checking won't
send anything twice. Deleting a preset deletes its alert.

The POST body is JSON with a `text` summary, so it renders as-is in a Slack
(or Slack-compatible) incoming webhook. The other fields are for anything
custom:

```json
{
  "text": "AlphaScanner: 2 new matches for preset 'surge3': ABC, XYZ",
  "preset": "surge3",
  "fetched_at": "2026-10-06T21:07:47+00:00",
  "match_count": 5,
  "new_count": 2,
  "new_matches": [
    {"coin_id": "...", "symbol": "...", "name": "...", "current_price": 0.42,
     "price_change_pct_24h": 18.2, "total_volume": 9200000,
     "volume_surge": 3.4, "market_cap": 310000000}
  ]
}
```

Because the server makes these requests, webhook URLs must be `http(s)` and
must resolve to **public** addresses. Private, loopback, and link-local hosts
(e.g. cloud metadata endpoints) are rejected when the alert is saved and again
at send time, and redirects aren't followed. The request then connects to the
exact address that was checked, so a DNS change between check and connect
(DNS rebinding) can't redirect it. If the server reaches the internet through
an `HTTPS_PROXY`, the proxy resolves the host instead. To post to something
on your own network (say, a local n8n), set
`ALPHASCANNER_ALERTS_ALLOW_PRIVATE_WEBHOOKS=true`.

Without `ALPHASCANNER_AUTH_PASSWORD`, anyone who can reach the web server can
create alerts, and the server logs a warning at startup saying so.
Webhook URLs often embed a secret token, so listings show only the host.

### Coin history

Click any coin in the dashboard (or open `/coin/COIN_ID`) for its price,
volume, and volume-surge charts across every stored snapshot. From the CLI:
`alphascanner history bitcoin --limit 50`. Each snapshot's volume surge is
computed against the coin's average volume over the previous
`ALPHASCANNER_HISTORY_WINDOW` snapshots (skipping any it was missing from),
the same baseline the dashboard uses, so the newest value matches it.

## API

- `GET /` — HTMX dashboard
- `GET /screen` — HTML table fragment (HTMX target)
- `GET /api/screen` — JSON, same query params as the CLI; add `?preset=NAME`
  to run a saved preset instead (the other params are then ignored)
- `GET /api/presets` — list saved presets
- `PUT /api/presets/{name}` — create/overwrite a preset; JSON body takes the
  same fields as `/api/screen`'s query params
- `DELETE /api/presets/{name}` — delete a preset (`204`, or `404` if missing)
- `GET /coin/{coin_id}` — coin history page
- `GET /api/coins/{coin_id}/history?limit=N` — same data as JSON, oldest
  first (`limit` 1–1000, default 200)
- `GET /alerts` — alerts table HTML fragment (HTMX target)
- `GET /api/alerts` — list alerts (webhook URLs masked to the host)
- `PUT /api/alerts/{preset}` — create an alert or change its webhook; JSON
  body `{"webhook_url": "https://..."}` (`404` if the preset doesn't exist,
  `422` for a rejected URL, including a bad port)
- `DELETE /api/alerts/{preset}` — remove an alert (`204`, or `404`)
- `GET /healthz`

## Configuration

All settings are env vars prefixed `ALPHASCANNER_` (see `.env.example`). The
free CoinGecko tier works without an API key. To use a Demo key, just set
`ALPHASCANNER_COINGECKO_API_KEY`. To use a Pro key, also set
`ALPHASCANNER_COINGECKO_BASE_URL=https://pro-api.coingecko.com/api/v3` — the
client picks the matching auth header (`x-cg-demo-api-key` /
`x-cg-pro-api-key`) from whichever base URL is configured.

By default the web UI/API have no authentication and no rate limit beyond a
generous default — fine for localhost-only use. If you expose this beyond
localhost:

- Set `ALPHASCANNER_AUTH_PASSWORD` (and optionally `ALPHASCANNER_AUTH_USERNAME`,
  default `admin`) to require HTTP Basic Auth on every route except
  `/healthz`, which is always open (docker-compose's healthcheck depends on
  it).
- `ALPHASCANNER_RATE_LIMIT_PER_MINUTE` (default 60) caps requests per client
  IP to those same routes; excess requests get a `429`. The limiter is
  in-memory and per-process, so it resets on restart and isn't shared across
  multiple workers/replicas.

`ALPHASCANNER_ALERTS_ALLOW_PRIVATE_WEBHOOKS` (default `false`) lets alert
webhooks target private/local addresses; see [Alerts](#alerts).

## Tests

```bash
pytest                                              # logic, db, api, cli, scheduler, presets, alerts, history
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
  scanner.py    # ranking / filter logic (pure pandas) + ScreenQuery validation
  presets.py    # saved filter presets
  alerts.py     # webhook alerts on presets (checked by the scheduler)
  cli.py        # Typer
  api.py        # FastAPI
  scheduler.py  # async loop
  templates/
tests/
scripts/
  hook_receiver.py  # local webhook receiver for trying out alerts
Dockerfile
docker-compose.yml
requirements-lock.txt  # pinned runtime deps used by the Docker build
```
