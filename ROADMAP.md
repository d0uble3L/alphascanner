# AlphaScanner Roadmap

Format: **Now / Next / Later**. Now = committed for the current sprint. Next
= planned, scoped, but timing is soft. Later = directional strategic bets,
not yet scoped.

Last updated: 2026-09-28.

## Status

Recently shipped:

- Dockerfile fix (was broken — Alpine base image using Debian-only
  `apt-get`/`useradd`), pinned runtime deps via `requirements-lock.txt`
- SQLite concurrency fix (WAL mode + busy timeout) so the web API no longer
  500s while the scheduler is mid-write
- Volume-surge divide-by-zero fix
- CoinGecko Pro API key support (was silently broken)
- Test coverage: 8 → 51 tests, covering `db.py`, `fetcher.py`, `api.py`,
  `cli.py`, `scheduler.py` (previously untested)
- Docker healthchecks for both `web` and `scheduler`
- Optional HTTP Basic Auth and per-IP rate limiting on the web UI/API

In flight:

- 3 Dependabot PRs bumping GitHub Actions versions (#5, #11, #13) — pure CI
  version bumps, no app code touched
- `.env.example` local-setup fix (PR #16)

## Now (this sprint)

| # | Item | What it is | Why now | Effort |
|---|------|------------|---------|--------|
| 1 | **Saved filter presets** | Name and recall a `FilterParams` combo (CLI flags / query params) instead of retyping every flag each time | Cheapest of the three, and item 2 is really "an alert = a saved preset + a notification" — sequencing this first avoids rework | S–M |
| 2 | **Threshold alerts** | A saved screen pushes a notification (webhook to start; Slack/email later) when it starts matching, instead of requiring someone to keep the dashboard open | This is the app's actual value prop — "catch moves before they're obvious" — but today it only works if a human is actively watching | M |
| 3 | **Per-coin history view** | A `/coin/{coin_id}` page charting price/volume/surge across the snapshots already being stored every 15 minutes | Cheapest high-value feature available — no new data collection, just a read path over data already being collected and currently discarded after the aggregate average | M |

**Sequencing note:** build #1 before #2 — alerts want the same "named
filter" concept presets need, so doing presets first turns alerts into
"attach a notification to an existing preset" rather than a parallel data
model.

## Next (1–3 months, directional)

- Multi-channel alert delivery (Slack / email / webhook) — extends item 2
  above once it exists
- Historical backtest view — "if you'd acted on this signal N snapshots ago,
  what happened after"
- Multi-user accounts, if this ever needs to serve more than one person
  (today's auth is a single shared password)

## Later (3–6mo+, strategic bets)

- Multi-exchange data beyond CoinGecko, for cross-venue liquidity/arbitrage
  signals
- Horizontal scaling — today's rate limiter and SQLite are single-process;
  would need rework to run multiple web workers

## Risks & Dependencies

- No team-size/capacity data exists for this project (assumed solo
  maintainer) — revisit the "Now" scope if that's wrong
- All 3 "Now" items are additive — no breaking changes to the existing
  scan/filter/CLI/API surface
