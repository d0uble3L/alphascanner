"""Threshold alerts: notify a webhook when coins start matching a saved preset."""

import ipaddress
import json
import logging
import math
import socket
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlsplit

import httpx

from .config import settings
from .db import connect
from .presets import is_valid_name, list_presets
from .scanner import scan

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


def validate_webhook_url(url: str) -> None:
    """Reject non-http(s) URLs and, unless allowed, hosts on private networks."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise InvalidWebhookURL("Webhook URL must be an http:// or https:// URL with a host.")
    if settings.alerts_allow_private_webhooks:
        return
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(parts.hostname, port, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError):
        raise InvalidWebhookURL(f"Could not resolve webhook host {parts.hostname!r}.") from None
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.version == 6 and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        if not ip.is_global or ip.is_multicast:
            raise InvalidWebhookURL(
                f"Webhook host {parts.hostname!r} resolves to a non-public address. "
                "Set ALPHASCANNER_ALERTS_ALLOW_PRIVATE_WEBHOOKS=true to allow it."
            )


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


def set_alert(preset_name: str, webhook_url: str) -> None:
    """Create an alert on a preset, or change an existing alert's webhook."""
    if not is_valid_name(preset_name):
        raise UnknownPreset(preset_name)
    validate_webhook_url(webhook_url)
    try:
        with connect() as conn:
            conn.execute(
                "INSERT INTO alerts (preset_name, webhook_url, created_at) VALUES (?,?,?) "
                "ON CONFLICT(preset_name) DO UPDATE SET webhook_url = excluded.webhook_url, "
                "last_error = NULL",
                (preset_name, webhook_url, _now()),
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


def _send(webhook_url: str, payload: dict) -> None:
    # Re-validate at send time: the host's DNS may have changed since the alert was saved.
    try:
        validate_webhook_url(webhook_url)
    except InvalidWebhookURL as exc:
        raise AlertDeliveryError(str(exc)) from None
    try:
        resp = httpx.post(webhook_url, json=payload, timeout=WEBHOOK_TIMEOUT_SECONDS)
    except httpx.HTTPError as exc:
        # Deliberately not str(exc): httpx messages include the URL, which may hold a token.
        raise AlertDeliveryError(f"{type(exc).__name__} while contacting webhook") from None
    if resp.is_redirect:
        raise AlertDeliveryError(f"Webhook redirected (HTTP {resp.status_code}); redirects are not followed")
    if resp.status_code >= 300:
        raise AlertDeliveryError(f"Webhook returned HTTP {resp.status_code}")


def _payload(preset_name: str, new_rows: list[dict], match_count: int, fetched_at: str) -> dict:
    symbols = ", ".join(str(r["symbol"]).upper() for r in new_rows)
    return {
        # "text" makes the payload render as-is in Slack-compatible incoming webhooks.
        "text": (
            f"AlphaScanner: {len(new_rows)} new match{'es' if len(new_rows) != 1 else ''} "
            f"for preset '{preset_name}': {symbols}"
        ),
        "preset": preset_name,
        "fetched_at": fetched_at,
        "match_count": match_count,
        "new_matches": [{k: _json_safe(r.get(k)) for k in _PAYLOAD_FIELDS} for r in new_rows],
    }


def check_alerts() -> list[CheckResult]:
    """Re-screen every alert's preset and notify on coins that newly match.

    "Newly" means not in the previous check's matches, so a coin that drops out
    and later comes back triggers again. On a failed delivery the previous
    matches are kept, so the same coins are retried on the next check.
    """
    presets = {p.name: p for p in list_presets()}
    results = []
    for alert in list_alerts():
        preset = presets.get(alert.preset_name)
        if preset is None:  # unreachable given the FK cascade, but don't crash the loop
            continue
        df, fetched_at = scan(preset.query.to_filter_params())
        if fetched_at is None:
            continue  # no snapshot data yet
        rows = df.to_dict(orient="records")
        current_ids = [r["coin_id"] for r in rows]
        previous = set(alert.last_matched)
        new_rows = [r for r in rows if r["coin_id"] not in previous]

        delivered, error = False, None
        if new_rows:
            try:
                _send(alert.webhook_url, _payload(alert.preset_name, new_rows, len(rows), fetched_at))
                delivered = True
            except AlertDeliveryError as exc:
                error = str(exc)
                log.warning(
                    "Alert %r delivery to %s failed: %s",
                    alert.preset_name, mask_url(alert.webhook_url), error,
                )

        now = _now()
        with connect() as conn:
            if error:
                conn.execute(
                    "UPDATE alerts SET last_checked_at = ?, last_error = ? WHERE preset_name = ?",
                    (now, error, alert.preset_name),
                )
            else:
                conn.execute(
                    "UPDATE alerts SET last_matched = ?, last_checked_at = ?, last_error = NULL, "
                    "last_notified_at = COALESCE(?, last_notified_at) WHERE preset_name = ?",
                    (json.dumps(current_ids), now, now if delivered else None, alert.preset_name),
                )
        results.append(CheckResult(alert.preset_name, len(rows), len(new_rows), delivered, error))
    return results
