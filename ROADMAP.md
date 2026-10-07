# AlphaScanner Roadmap

Format: **Now / Next / Later**. Now = committed for the current sprint. Next
= planned, scoped, but timing is soft. Later = directional strategic bets,
not yet scoped.

Last updated: 2026-10-07.

## Status

Recently shipped:

- **Alerts in the web UI** (sprint 2, item 1; the PR adding this update): an
  Alerts panel under the preset bar to set an alert on the selected preset,
  see each alert's masked webhook, last check, last notification, and last
  error, and delete alerts. It refreshes every minute and opens on its own
  with a red count when an alert is failing
- **Alert + history hardening** (#31), from a code review of #30:
  - Alerts compare every coin passing a preset's filters, not just its top
    `limit`, so rank changes no longer trigger notifications. The limit now
    only caps how many coins a notification lists (`new_count` gives the
    total)
  - Webhook requests connect to the exact IP that passed the public-address
    check, closing the DNS-rebinding gap listed under Risks
  - A webhook URL with an invalid port is rejected with a 422 instead of
    causing a 500
  - One failing alert no longer stops the remaining alerts from being checked
  - A concurrent `alert check` and scheduler run can't send the same coins
    twice (compare-and-swap on `last_matched`)
  - New alerts start from the coins matching at creation instead of
    notifying about all of them on the first check
  - The latest snapshot and volume surge are computed once per check, not
    once per alert
  - Coin-history surge uses the main screen's baseline, so the two agree
    even for coins missing from some snapshots
  - Startup warning when auth is off, since anyone who can reach the server
    can then create alerts
- **Sprint 1 complete** — all three items below (presets #24; alerts and
  coin history #30)
- Docker base image back on `python:3.12-alpine` (#26) after a Snyk auto-fix
  moved it to a Python 3.15 release candidate and broke the build
- Scheduler heartbeat path derived from `db_path`, undocumented env override
  removed (#27, Snyk CWE-23)
- Earlier: Dockerfile fix, pinned deps (`requirements-lock.txt`), SQLite
  concurrency fix, volume-surge divide-by-zero fix, CoinGecko Pro key support,
  Docker healthchecks, optional Basic Auth + rate limiting, test coverage from
  8 to 100+ tests

- Runtime dependency bumps merged (#19–#23: fastapi 0.142.2, uvicorn 0.54.0,
  pandas 3.0.6, idna 3.20, starlette 1.7.0); the test suite passes on them
- Snyk's Python 3.15 RC base-image PR (#29) closed unmerged; the Dockerfile
  stays on `python:3.12-alpine`

In flight — awaiting review/approval to merge (branch protection requires 1
approval):

- `.env.example` local-setup fix (#16), green on CI
- 3 Dependabot PRs bumping GitHub Actions versions (#5, #11, #13) — pure CI
  version bumps, no app code touched, green on CI

## Done (sprint 1)

| # | Item | Shipped |
|---|------|---------|
| 1 | **Saved filter presets** | CLI (`--save-as`, `--preset`, `preset list/delete`), API (`/api/presets`, `?preset=`), web UI preset bar (#24) |
| 2 | **Threshold alerts** | `alphascanner alert set/list/check/delete` and `/api/alerts`; the scheduler checks every alert after each fetch and POSTs only newly matching coins to a webhook (Slack-compatible `text` field). Failed deliveries are recorded and retried. Webhooks must resolve to public addresses unless explicitly allowed; URLs are masked in listings |
| 3 | **Per-coin history view** | `/coin/{coin_id}` page with price/volume/surge sparklines (server-rendered SVG, no JS library), `/api/coins/{coin_id}/history`, `alphascanner history`; dashboard rows link to it |

## Now (sprint 2)

Follow-ups surfaced while building sprint 1. Item 1 is done; 2 and 3 are
still proposed. Confirm or reshuffle before starting them.

| # | Item | What it is | Why now | Effort | Status |
|---|------|------------|---------|--------|--------|
| 1 | **Alerts in the web UI** | Create/delete alerts and see last check, notification, and error next to the preset bar | Alerts are CLI/API-only today, so the dashboard user can't see when one is failing | S | **Done** (this PR) |
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
- Snyk keeps proposing the Python 3.15 release-candidate base image (#25
  broke the build when merged, #28 was merged as a no-op, #29 was closed).
  Consider configuring Snyk to stay on Python 3.12 so these stop arriving
- Webhook DNS-rebinding protection doesn't apply when the server sends
  through an `HTTPS_PROXY`, because the proxy resolves the host. That's
  acceptable for a self-hosted tool; revisit before adding multi-user
  accounts
- With auth off (no `ALPHASCANNER_AUTH_PASSWORD`), anyone who can reach the
  server can create alerts. The server now warns about this at startup;
  consider requiring a password for the alert endpoints before adding
  multi-user accounts
