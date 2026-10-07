"""Threshold alerts: notify a webhook when coins start matching a saved preset."""

import ipaddress
import json
import logging
import math
import socket
import sqlite3
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlsplit

import httpx

from .config import settings
from .db import connect
from .presets import Preset, is_valid_name, list_presets
from .scanner import latest_with_surge, matching_rows

log = logging.getLogger(__name__)

WEBHOOK_TIMEOUT_SECONDS = 10
_PAYLOAD_FIELDS = [
    "coin_id",
    "symbol",
    "name",
    "current_price",
    "price_change_pct_24h",
    "total_volume",
    "volume_surge",
    "market_cap",
]


class InvalidWebhookURL(ValueError):
    pass


class UnknownPreset(LookupError):
    pass


class AlertDeliveryError(RuntimeError):
    pass


@dataclass
class Alert:
    preset_name: str
    webhook_url: str
    last_matched: list[str]
    last_checked_at: str | None
    last_notified_at: str | None
    last_error: str | None
    created_at: str


@dataclass
class CheckResult:
    preset_name: str
    matched: int
    new: int
    delivered: bool
    error: str | None


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def mask_url(url: str) -> str:
    """Webhook URLs often embed a secret token (e.g. Slack); show only the host."""
    parts = urlsplit(url)
    host = parts.hostname or "?"
    if parts.port:
        host = f"{host}:{parts.port}"
    suffix = "/…" if parts.path not in ("", "/") or parts.query else ""
    return f"{parts.scheme}://{host}{suffix}"


def validate_webhook_url(url: str) -> str | None:
    """Reject non-http(s) URLs and, unless allowed, hosts on private networks.

    Returns the checked public IP to connect to, or None when private webhooks
    are allowed (nothing to pin).
    """
    parts = urlsplit(url)
    try:
        port = parts.port
    except ValueError:  # out of range or not a number
        raise InvalidWebhookURL("Webhook URL has an invalid port.") from None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise InvalidWebhookURL("Webhook URL must be an http:// or https:// URL with a host.")
    if settings.alerts_allow_private_webhooks:
        return None
    port = port or (443 if parts.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(parts.hostname, port, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError):
        raise InvalidWebhookURL(f"Could not resolve webhook host {parts.hostname!r}.") from None
    ips = []
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.version == 6 and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        if not ip.is_global or ip.is_multicast:
            raise InvalidWebhookURL(
                f"Webhook host {parts.hostname!r} resolves to a non-public address. "
                "Set ALPHASCANNER_ALERTS_ALLOW_PRIVATE_WEBHOOKS=true to allow it."
            )
        ips.append(str(ip))
    if not ips:
        raise InvalidWebhookURL(f"Could not resolve webhook host {parts.hostname!r}.")
    return ips[0]


def _from_row(row) -> Alert:
    return Alert(
        preset_name=row["preset_name"],
        webhook_url=row["webhook_url"],
        last_matched=json.loads(row["last_matched"]),
        last_checked_at=row["last_checked_at"],
        last_notified_at=row["last_notified_at"],
        last_error=row["last_error"],
        created_at=row["created_at"],
    )


def _current_matches(preset: Preset, enriched) -> list[dict]:
    """Every coin passing the preset's filters, sorted, ignoring its row limit.

    The limit is a display cap. Applying it here would make a coin moving from
    rank N+1 to N look like a new match although it passed the filters all along.
    """
    return matching_rows(enriched, preset.query.to_filter_params()).to_dict(orient="records")


def set_alert(preset_name: str, webhook_url: str) -> None:
    """Create an alert on a preset, or change an existing alert's webhook.

    A new alert starts from the coins matching right now, so it only notifies
    about coins that start matching later rather than everything at once.
    """
    # The name comes from the request path. Match it against the stored presets
    # instead of querying by it, so request input never reaches a SQL statement;
    # every value written below comes from the database or is validated.
    if not is_valid_name(preset_name):
        raise UnknownPreset(preset_name)
    preset = next((p for p in list_presets() if p.name == preset_name), None)
    if preset is None:
        raise UnknownPreset(preset_name)
    validate_webhook_url(webhook_url)
    enriched, fetched_at = latest_with_surge()
    seed = [r["coin_id"] for r in _current_matches(preset, enriched)] if fetched_at else []
    try:
        with connect() as conn:
            # On conflict last_matched is left alone: changing the webhook
            # shouldn't re-notify or swallow anything.
            conn.execute(
                "INSERT INTO alerts (preset_name, webhook_url, last_matched, created_at) "
                "VALUES (?,?,?,?) "
                "ON CONFLICT(preset_name) DO UPDATE SET webhook_url = excluded.webhook_url, "
                "last_error = NULL",
                (preset.name, webhook_url, json.dumps(seed), _now()),
            )
    except sqlite3.IntegrityError:
        # The foreign key on presets(name) rejects alerts on presets that don't exist.
        raise UnknownPreset(preset_name) from None


def list_alerts() -> list[Alert]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM alerts ORDER BY preset_name").fetchall()
    return [_from_row(r) for r in rows]


def delete_alert(preset_name: str) -> bool:
    if not is_valid_name(preset_name):
        return False
    with connect() as conn:
        cur = conn.execute("DELETE FROM alerts WHERE preset_name = ?", (preset_name,))
    return cur.rowcount > 0


def _json_safe(v):
    if isinstance(v, float) and math.isnan(v):
        return None
    return v


def _uses_proxy(url: httpx.URL) -> bool:
    """Whether httpx would send this request through a proxy from the environment."""
    proxies = urllib.request.getproxies()
    if not (proxies.get(url.scheme) or proxies.get("all")):
        return False
    return not urllib.request.proxy_bypass(url.host)


def _http_post(url: httpx.URL, payload: dict, headers: dict, extensions: dict) -> httpx.Response:
    with httpx.Client(timeout=WEBHOOK_TIMEOUT_SECONDS) as client:
        return client.post(url, json=payload, headers=headers, extensions=extensions)


def _send(webhook_url: str, payload: dict) -> None:
    # Re-validate at send time: the host's DNS may have changed since the alert was saved.
    try:
        pinned_ip = validate_webhook_url(webhook_url)
    except InvalidWebhookURL as exc:
        raise AlertDeliveryError(str(exc)) from None
    url = httpx.URL(webhook_url)
    headers, extensions = {}, {}
    if pinned_ip and not _uses_proxy(url):
        # Connect to the address just checked instead of letting httpx resolve
        # the name again, so a DNS change in between (rebinding) can't redirect
        # the request inside the network. Host and TLS SNI/certificate checks
        # still use the real hostname. Through a proxy the proxy resolves the
        # name, and pinning would break its TLS hostname check, so it's skipped.
        headers["Host"] = url.netloc.decode("ascii")
        extensions["sni_hostname"] = url.host
        url = url.copy_with(host=pinned_ip)
    try:
        resp = _http_post(url, payload, headers, extensions)
    except httpx.HTTPError as exc:
        # Deliberately not str(exc): httpx messages include the URL, which may hold a token.
        raise AlertDeliveryError(f"{type(exc).__name__} while contacting webhook") from None
    if resp.is_redirect:
        raise AlertDeliveryError(f"Webhook redirected (HTTP {resp.status_code}); redirects are not followed")
    if resp.status_code >= 300:
        raise AlertDeliveryError(f"Webhook returned HTTP {resp.status_code}")


def _payload(
    preset_name: str, new_rows: list[dict], match_count: int, fetched_at: str, max_listed: int
) -> dict:
    listed = new_rows[:max_listed]
    symbols = ", ".join(str(r["symbol"]).upper() for r in listed)
    more = len(new_rows) - len(listed)
    return {
        # "text" makes the payload render as-is in Slack-compatible incoming webhooks.
        "text": (
            f"AlphaScanner: {len(new_rows)} new match{'es' if len(new_rows) != 1 else ''} "
            f"for preset '{preset_name}': {symbols}" + (f" (+{more} more)" if more else "")
        ),
        "preset": preset_name,
        "fetched_at": fetched_at,
        "match_count": match_count,
        "new_count": len(new_rows),
        # Capped at the preset's row limit, in the preset's sort order.
        "new_matches": [{k: _json_safe(r.get(k)) for k in _PAYLOAD_FIELDS} for r in listed],
    }


def _check_one(alert: Alert, preset: Preset, enriched, fetched_at: str) -> CheckResult | None:
    rows = _current_matches(preset, enriched)
    previous_json = json.dumps(alert.last_matched)
    current_json = json.dumps([r["coin_id"] for r in rows])
    previous = set(alert.last_matched)
    new_rows = [r for r in rows if r["coin_id"] not in previous]
    name = alert.preset_name

    # Claim this check with a compare-and-swap on last_matched. If the
    # scheduler and `alphascanner alert check` run at once, only one update
    # lands, so the same coins are never sent twice.
    with connect() as conn:
        claimed = conn.execute(
            "UPDATE alerts SET last_matched = ?, last_checked_at = ? "
            "WHERE preset_name = ? AND last_matched = ?",
            (current_json, _now(), name, previous_json),
        ).rowcount
    if not claimed:
        return None  # another check got there first, or the alert was deleted

    delivered, error = False, None
    if new_rows:
        try:
            _send(alert.webhook_url, _payload(name, new_rows, len(rows), fetched_at, preset.query.limit))
            delivered = True
        except AlertDeliveryError as exc:
            error = str(exc)
            log.warning("Alert %r delivery to %s failed: %s", name, mask_url(alert.webhook_url), error)
        except Exception as exc:
            # Still restore last_matched below, or these coins would never be sent.
            log.exception("Alert %r delivery to %s failed", name, mask_url(alert.webhook_url))
            error = f"{type(exc).__name__} while sending"

    with connect() as conn:
        if error:
            # Put the previous matches back so the same coins are retried next check.
            conn.execute(
                "UPDATE alerts SET last_matched = ?, last_error = ? "
                "WHERE preset_name = ? AND last_matched = ?",
                (previous_json, error, name, current_json),
            )
        else:
            conn.execute(
                "UPDATE alerts SET last_error = NULL, "
                "last_notified_at = COALESCE(?, last_notified_at) WHERE preset_name = ?",
                (_now() if delivered else None, name),
            )
    return CheckResult(name, len(rows), len(new_rows), delivered, error)


def check_alerts() -> list[CheckResult]:
    """Re-screen every alert's preset and notify on coins that newly match.

    "Newly" means not in the previous check's matches, so a coin that drops out
    and later comes back triggers again. On a failed delivery the previous
    matches are kept, so the same coins are retried on the next check. One
    alert failing unexpectedly doesn't stop the others from being checked.
    """
    alerts = list_alerts()
    if not alerts:
        return []
    # One snapshot read and surge computation shared by every alert.
    enriched, fetched_at = latest_with_surge()
    if fetched_at is None:
        return []  # no snapshot data yet
    presets = {p.name: p for p in list_presets()}
    results = []
    for alert in alerts:
        preset = presets.get(alert.preset_name)
        if preset is None:  # deleted since list_alerts(); the FK cascade removed the alert
            continue
        try:
            result = _check_one(alert, preset, enriched, fetched_at)
        except Exception as exc:
            log.exception("Alert %r check failed", alert.preset_name)
            # Only the type: messages can carry details such as the webhook URL.
            result = CheckResult(alert.preset_name, 0, 0, False, f"{type(exc).__name__} during check")
            try:
                with connect() as conn:
                    conn.execute(
                        "UPDATE alerts SET last_checked_at = ?, last_error = ? WHERE preset_name = ?",
                        (_now(), result.error, alert.preset_name),
                    )
            except sqlite3.Error:
                log.exception("Could not record the failure of alert %r", alert.preset_name)
        if result is not None:
            results.append(result)
    return results
