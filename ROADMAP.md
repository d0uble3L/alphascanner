# AlphaScanner Roadmap

Format: **Now / Next / Later**. Now = committed for the current sprint. Next
= planned, scoped, but timing is soft. Later = directional strategic bets,
not yet scoped.

Last updated: 2026-10-06.

## Status

Recently shipped:

- **Sprint 1 complete** — all three items below (presets #24; alerts and
  coin history in the PR adding this update)
- Docker base image back on `python:3.12-alpine` (#26) after a Snyk auto-fix
  moved it to a Python 3.15 release candidate and broke the build
- Scheduler heartbeat path derived from `db_path`, undocumented env override
  removed (#27, Snyk CWE-23)
- Earlier: Dockerfile fix, pinned deps (`requirements-lock.txt`), SQLite
  concurrency fix, volume-surge divide-by-zero fix, CoinGecko Pro key support,
  Docker healthchecks, optional Basic Auth + rate limiting, test coverage from
  8 to 100+ tests

In flight — awaiting review/approval to merge (branch protection requires 1
approval):

- `.env.example` local-setup fix (#16), green on CI
- 3 Dependabot PRs bumping GitHub Actions versions (#5, #11, #13) — pure CI
  version bumps, no app code touched, green on CI
- 5 Dependabot PRs bumping pinned runtime deps in `requirements-lock.txt`
  (#19–#23: fastapi, uvicorn, pandas, idna, starlette), not yet reviewed
- **Do not merge #28** — Snyk re-proposing the `python:3.15-rc-alpine3.22`
  base image that #26 reverted (it breaks `docker build`; the CVEs it cites
  are already patched by `apk upgrade`). Close it.

## Done (sprint 1)

| # | Item | Shipped |
|---|------|---------|
| 1 | **Saved filter presets** | CLI (`--save-as`, `--preset`, `preset list/delete`), API (`/api/presets`, `?preset=`), web UI preset bar (#24) |
| 2 | **Threshold alerts** | `alphascanner alert set/list/check/delete` and `/api/alerts`; the scheduler checks every alert after each fetch and POSTs only newly matching coins to a webhook (Slack-compatible `text` field). Failed deliveries are recorded and retried. Webhooks must resolve to public addresses unless explicitly allowed; URLs are masked in listings |
| 3 | **Per-coin history view** | `/coin/{coin_id}` page with price/volume/surge sparklines (server-rendered SVG, no JS library), `/api/coins/{coin_id}/history`, `alphascanner history`; dashboard rows link to it |

## Now (sprint 2) — proposed, not yet committed

Follow-ups surfaced while building sprint 1. Confirm or reshuffle before
starting.

| # | Item | What it is | Why now | Effort | Status |
|---|------|------------|---------|--------|--------|
| 1 | **Alerts in the web UI** | Create/delete alerts and see last check, notification, and error next to the preset bar | Alerts are CLI/API-only today, so the dashboard user can't see when one is failing | S | **Proposed** |
| 2 | **Email + Discord alert delivery** | Delivery channels beyond a generic/Slack webhook | Slack already works via the payload's `text` field; email reaches people who don't run a chat workspace | M | **Proposed** |
| 3 | **Signal backtest view** | "If you'd acted on this preset N snapshots ago, what happened after" — per-preset hit rate using the stored history | The history data and per-snapshot surge from item 3 make this mostly a new read path | M–L | **Proposed** |

## Next (1–3 months, directional)

- Multi-user accounts, if this ever needs to serve more than one person
  (today's auth is a single shared password)
- Data retention: snapshots grow ~1,000 rows every 15 minutes with no
  pruning; add a retention window or downsampling once history views exist
  that depend on it

## Later (3–6mo+, strategic bets)

- Multi-exchange data beyond CoinGecko, for cross-venue liquidity/arbitrage
  signals
- Horizontal scaling — today's rate limiter and SQLite are single-process;
  would need rework to run multiple web workers

## Risks & Dependencies

- No team-size/capacity data exists for this project (assumed solo
  maintainer) — revisit the "Now" scope if that's wrong
- Snyk keeps proposing the Python 3.15 release-candidate base image (#25 was
  merged and broke the build; #28 repeats it). Consider configuring Snyk to
  stay on Python 3.12 so these stop arriving
- Alert webhook validation resolves DNS at save and send time, but the HTTP
  client resolves again when connecting, so a host that changes DNS in that
  window (DNS rebinding) isn't fully covered. Acceptable for a self-hosted,
  single-user tool; revisit before multi-user accounts
