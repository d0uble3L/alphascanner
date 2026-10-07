import asyncio
import json
import socket

import httpx
import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

import alphascanner.alerts as alerts_module
from alphascanner import scheduler
from alphascanner.alerts import (
    InvalidWebhookURL,
    UnknownPreset,
    check_alerts,
    delete_alert,
    list_alerts,
    mask_url,
    set_alert,
    validate_webhook_url,
)
from alphascanner.api import app
from alphascanner.cli import app as cli_app
from alphascanner.config import settings
from alphascanner.db import connect
from alphascanner.presets import delete_preset, get_preset, save_preset
from alphascanner.scanner import ScreenQuery

HOOK = "https://hooks.example.com/services/T000/SECRET-TOKEN"


@pytest.fixture
def dns(monkeypatch):
    """Fake DNS: hostname -> list of IPs. Unknown hosts fail to resolve."""
    table = {"hooks.example.com": ["93.184.216.34"]}

    def fake_getaddrinfo(host, port, *a, **k):
        if host not in table:
            raise socket.gaierror("not found")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in table[host]]

    monkeypatch.setattr(alerts_module.socket, "getaddrinfo", fake_getaddrinfo)
    return table


@pytest.fixture
def webhook(monkeypatch, dns):
    """Records POSTs; set .status / .error to simulate failures."""

    class Recorder:
        def __init__(self):
            self.calls: list[dict] = []
            self.status = 200
            self.error: Exception | None = None

    rec = Recorder()

    def fake_post(url, payload, headers, extensions):
        if rec.error:
            raise rec.error
        rec.calls.append({"url": str(url), "json": payload, "headers": headers, "extensions": extensions})
        return httpx.Response(rec.status, request=httpx.Request("POST", url))

    monkeypatch.setattr(alerts_module, "_http_post", fake_post)
    monkeypatch.setattr(alerts_module, "_uses_proxy", lambda url: False)
    return rec


def _snapshot(db_path, fetched_at, coins):
    with connect(db_path) as conn:
        for coin_id, volume in coins:
            conn.execute(
                "INSERT INTO snapshots (coin_id, symbol, name, current_price, total_volume, "
                "fetched_at) VALUES (?,?,?,?,?,?)",
                (coin_id, coin_id[:3], coin_id.title(), 1.5, volume, fetched_at),
            )


def _big_volume_preset():
    save_preset("big", ScreenQuery(min_volume=100, sort_by="volume"))


def _sent_ids(rec, i=-1):
    return [m["coin_id"] for m in rec.calls[i]["json"]["new_matches"]]


# --- URL handling -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "masked"),
    [
        (HOOK, "https://hooks.example.com/…"),
        ("https://hooks.example.com", "https://hooks.example.com"),
        ("http://host.example:8080/x?token=1", "http://host.example:8080/…"),
    ],
)
def test_mask_url_hides_path_and_query(url, masked):
    assert mask_url(url) == masked


@pytest.mark.parametrize("url", ["ftp://hooks.example.com/x", "hooks.example.com/x", "https:///x", ""])
def test_rejects_non_http_urls(dns, url):
    with pytest.raises(InvalidWebhookURL):
        validate_webhook_url(url)


@pytest.mark.parametrize(
    "ip", ["127.0.0.1", "10.0.0.5", "192.168.1.1", "169.254.169.254", "::1", "fd00::1", "::ffff:127.0.0.1"]
)
def test_rejects_hosts_resolving_to_non_public_addresses(dns, ip):
    dns["internal.example"] = [ip]
    with pytest.raises(InvalidWebhookURL, match="non-public"):
        validate_webhook_url("https://internal.example/hook")


def test_rejects_if_any_resolved_address_is_private(dns):
    dns["mixed.example"] = ["93.184.216.34", "10.0.0.1"]
    with pytest.raises(InvalidWebhookURL):
        validate_webhook_url("https://mixed.example/hook")


def test_rejects_unresolvable_host(dns):
    with pytest.raises(InvalidWebhookURL, match="resolve"):
        validate_webhook_url("https://nowhere.example/hook")


@pytest.mark.parametrize("url", ["https://hooks.example.com:99999/x", "https://hooks.example.com:abc/x"])
def test_rejects_invalid_port(dns, url):
    with pytest.raises(InvalidWebhookURL, match="port"):
        validate_webhook_url(url)


def test_accepts_public_host_and_returns_the_ip_to_pin(dns):
    assert validate_webhook_url(HOOK) == "93.184.216.34"


def test_private_hosts_allowed_when_opted_in(dns, monkeypatch):
    monkeypatch.setattr(settings, "alerts_allow_private_webhooks", True)
    assert validate_webhook_url("http://localhost:5678/webhook") is None  # no DNS lookup at all


# --- CRUD -------------------------------------------------------------------------


def test_set_list_delete(db_path, dns):
    _big_volume_preset()
    set_alert("big", HOOK)
    [alert] = list_alerts()
    assert (alert.preset_name, alert.webhook_url, alert.last_matched) == ("big", HOOK, [])
    assert delete_alert("big") is True
    assert list_alerts() == []
    assert delete_alert("big") is False


def test_alert_requires_existing_preset(db_path, dns):
    with pytest.raises(UnknownPreset):
        set_alert("missing", HOOK)
    with pytest.raises(UnknownPreset):
        set_alert("bad name", HOOK)


@pytest.mark.parametrize("name", ["x' OR '1'='1", "big'; DROP TABLE presets; --", "big%27"])
def test_injection_style_preset_names_are_rejected(db_path, dns, name):
    _big_volume_preset()
    with pytest.raises(UnknownPreset):
        set_alert(name, HOOK)
    assert list_alerts() == []
    assert get_preset("big") is not None


def test_invalid_url_is_not_saved(db_path, dns):
    _big_volume_preset()
    with pytest.raises(InvalidWebhookURL):
        set_alert("big", "ftp://hooks.example.com")
    assert list_alerts() == []


def test_deleting_preset_deletes_its_alert(db_path, dns):
    _big_volume_preset()
    set_alert("big", HOOK)
    delete_preset("big")
    assert list_alerts() == []


def test_overwriting_preset_keeps_its_alert(db_path, dns):
    _big_volume_preset()
    set_alert("big", HOOK)
    save_preset("big", ScreenQuery(min_volume=500))  # upsert, not delete+insert
    assert [a.preset_name for a in list_alerts()] == ["big"]


# --- check_alerts -----------------------------------------------------------------


def test_notifies_only_on_newly_matching_coins(db_path, webhook):
    _big_volume_preset()
    set_alert("big", HOOK)
    _snapshot(db_path, "t1", [("bitcoin", 500), ("ethereum", 200), ("dust", 5)])

    [r] = check_alerts()
    assert (r.matched, r.new, r.delivered, r.error) == (2, 2, True, None)
    assert _sent_ids(webhook) == ["bitcoin", "ethereum"]

    # Same matches next check: no webhook.
    _snapshot(db_path, "t2", [("bitcoin", 500), ("ethereum", 200), ("dust", 5)])
    [r] = check_alerts()
    assert (r.new, r.delivered) == (0, False)
    assert len(webhook.calls) == 1

    # Only the coin that newly crosses the threshold is sent.
    _snapshot(db_path, "t3", [("bitcoin", 500), ("ethereum", 200), ("dust", 150)])
    check_alerts()
    assert _sent_ids(webhook) == ["dust"]


def test_coin_that_drops_out_and_returns_triggers_again(db_path, webhook):
    _big_volume_preset()
    set_alert("big", HOOK)
    _snapshot(db_path, "t1", [("dust", 150)])
    check_alerts()
    _snapshot(db_path, "t2", [("dust", 5)])
    check_alerts()
    _snapshot(db_path, "t3", [("dust", 150)])
    check_alerts()
    assert len(webhook.calls) == 2
    assert _sent_ids(webhook) == ["dust"]


def test_payload_shape(db_path, webhook):
    _big_volume_preset()
    set_alert("big", HOOK)
    _snapshot(db_path, "t1", [("bitcoin", 500)])
    check_alerts()
    call = webhook.calls[0]
    assert call["url"] == "https://93.184.216.34/services/T000/SECRET-TOKEN"  # pinned to checked IP
    assert call["headers"] == {"Host": "hooks.example.com"}
    assert call["extensions"] == {"sni_hostname": "hooks.example.com"}
    payload = call["json"]
    assert payload["text"] == "AlphaScanner: 1 new match for preset 'big': BIT"
    assert payload["preset"] == "big"
    assert payload["fetched_at"] == "t1"
    assert payload["match_count"] == 1
    assert payload["new_count"] == 1
    match = payload["new_matches"][0]
    assert match["coin_id"] == "bitcoin"
    assert match["volume_surge"] is None  # NaN (single snapshot) serialized as null
    json.dumps(payload, allow_nan=False)  # valid strict JSON


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (500, "Webhook returned HTTP 500"),
        (302, "redirects are not followed"),
    ],
)
def test_failed_delivery_records_error_and_retries_next_check(db_path, webhook, status, error):
    _big_volume_preset()
    set_alert("big", HOOK)
    _snapshot(db_path, "t1", [("bitcoin", 500)])

    webhook.status = status
    [r] = check_alerts()
    assert not r.delivered and error in r.error
    [a] = list_alerts()
    assert a.last_matched == [] and error in a.last_error and a.last_notified_at is None

    webhook.status = 200
    [r] = check_alerts()
    assert r.delivered and r.new == 1
    [a] = list_alerts()
    assert a.last_matched == ["bitcoin"] and a.last_error is None and a.last_notified_at


def test_transport_errors_never_leak_the_webhook_url(db_path, webhook):
    _big_volume_preset()
    set_alert("big", HOOK)
    _snapshot(db_path, "t1", [("bitcoin", 500)])
    webhook.error = httpx.ConnectError(f"failed to connect to {HOOK}")
    [r] = check_alerts()
    assert r.error == "ConnectError while contacting webhook"
    assert "SECRET-TOKEN" not in list_alerts()[0].last_error


def test_url_is_revalidated_at_send_time(db_path, webhook, dns):
    _big_volume_preset()
    set_alert("big", HOOK)
    dns["hooks.example.com"] = ["10.0.0.7"]  # DNS now points inside the network
    _snapshot(db_path, "t1", [("bitcoin", 500)])
    [r] = check_alerts()
    assert not r.delivered and "non-public" in r.error
    assert webhook.calls == []


def test_no_snapshot_data_means_nothing_to_check(db_path, webhook):
    _big_volume_preset()
    set_alert("big", HOOK)
    assert check_alerts() == []
    assert webhook.calls == []


def test_rank_changes_within_the_filters_do_not_notify(db_path, webhook):
    # limit=1 shows only the top coin, but both pass the filters all along.
    save_preset("top", ScreenQuery(min_volume=100, sort_by="volume", limit=1))
    set_alert("top", HOOK)
    _snapshot(db_path, "t1", [("bitcoin", 500), ("ethereum", 200)])
    [r] = check_alerts()
    assert (r.matched, r.new) == (2, 2)

    _snapshot(db_path, "t2", [("bitcoin", 200), ("ethereum", 500)])  # swap ranks
    [r] = check_alerts()
    assert (r.new, r.delivered) == (0, False)
    assert len(webhook.calls) == 1


def test_payload_lists_at_most_the_preset_limit(db_path, webhook):
    save_preset("top", ScreenQuery(min_volume=100, sort_by="volume", limit=1))
    set_alert("top", HOOK)
    _snapshot(db_path, "t1", [("bitcoin", 500), ("ethereum", 200)])
    [r] = check_alerts()
    assert r.new == 2
    payload = webhook.calls[0]["json"]
    assert payload["new_count"] == 2
    assert _sent_ids(webhook) == ["bitcoin"]  # highest volume first
    assert payload["text"].endswith(": BIT (+1 more)")


def test_new_alert_starts_from_current_matches(db_path, webhook):
    _big_volume_preset()
    _snapshot(db_path, "t1", [("bitcoin", 500)])
    set_alert("big", HOOK)
    assert list_alerts()[0].last_matched == ["bitcoin"]

    [r] = check_alerts()
    assert (r.matched, r.new) == (1, 0)
    assert webhook.calls == []

    _snapshot(db_path, "t2", [("bitcoin", 500), ("ethereum", 200)])
    check_alerts()
    assert _sent_ids(webhook) == ["ethereum"]


def test_changing_the_webhook_keeps_pending_matches(db_path, webhook):
    _big_volume_preset()
    set_alert("big", HOOK)
    _snapshot(db_path, "t1", [("bitcoin", 500)])
    webhook.status = 500
    check_alerts()  # fails; bitcoin still pending

    webhook.status = 200
    other = "https://hooks.example.com/services/OTHER"
    set_alert("big", other)
    [a] = list_alerts()
    assert a.last_matched == [] and a.last_error is None
    check_alerts()
    assert webhook.calls[-1]["url"].endswith("/services/OTHER")
    assert _sent_ids(webhook) == ["bitcoin"]


def test_one_failing_alert_does_not_stop_the_others(db_path, webhook, monkeypatch):
    save_preset("a", ScreenQuery(min_volume=100))
    save_preset("b", ScreenQuery(min_volume=100))
    set_alert("a", HOOK)
    set_alert("b", HOOK)
    _snapshot(db_path, "t1", [("bitcoin", 500)])

    real = alerts_module._current_matches

    def flaky(preset, enriched):
        if preset.name == "a":
            raise RuntimeError(f"boom {HOOK}")
        return real(preset, enriched)

    monkeypatch.setattr(alerts_module, "_current_matches", flaky)
    ra, rb = check_alerts()
    assert ra.error == "RuntimeError during check" and not ra.delivered
    assert rb.delivered and rb.error is None
    by_name = {a.preset_name: a for a in list_alerts()}
    assert by_name["a"].last_error == "RuntimeError during check"
    assert "SECRET-TOKEN" not in by_name["a"].last_error


def test_unexpected_send_error_keeps_coins_pending(db_path, webhook):
    _big_volume_preset()
    set_alert("big", HOOK)
    _snapshot(db_path, "t1", [("bitcoin", 500)])
    webhook.error = ValueError("unexpected")
    [r] = check_alerts()
    assert r.error == "ValueError while sending"
    assert list_alerts()[0].last_matched == []

    webhook.error = None
    [r] = check_alerts()
    assert r.delivered and _sent_ids(webhook) == ["bitcoin"]


def test_concurrent_checks_send_once(db_path, webhook):
    _big_volume_preset()
    set_alert("big", HOOK)
    _snapshot(db_path, "t1", [("bitcoin", 500)])
    [stale] = list_alerts()  # what a second, concurrent checker read

    assert len(check_alerts()) == 1
    enriched, fetched_at = alerts_module.latest_with_surge()
    preset = get_preset("big")
    assert alerts_module._check_one(stale, preset, enriched, fetched_at) is None
    assert len(webhook.calls) == 1


def test_not_pinned_when_private_webhooks_allowed(db_path, webhook, monkeypatch):
    monkeypatch.setattr(settings, "alerts_allow_private_webhooks", True)
    _big_volume_preset()
    set_alert("big", "http://localhost:5678/webhook")
    _snapshot(db_path, "t1", [("bitcoin", 500)])
    check_alerts()
    call = webhook.calls[0]
    assert call["url"] == "http://localhost:5678/webhook"
    assert call["headers"] == {} and call["extensions"] == {}


def test_not_pinned_through_a_proxy(db_path, webhook, monkeypatch):
    monkeypatch.setattr(alerts_module, "_uses_proxy", lambda url: True)
    _big_volume_preset()
    set_alert("big", HOOK)
    _snapshot(db_path, "t1", [("bitcoin", 500)])
    check_alerts()
    assert webhook.calls[0]["url"] == HOOK


def test_pinned_host_header_keeps_a_non_default_port(db_path, webhook):
    _big_volume_preset()
    set_alert("big", "https://hooks.example.com:8443/x")
    _snapshot(db_path, "t1", [("bitcoin", 500)])
    check_alerts()
    call = webhook.calls[0]
    assert call["url"] == "https://93.184.216.34:8443/x"
    assert call["headers"] == {"Host": "hooks.example.com:8443"}


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({}, False),
        ({"HTTPS_PROXY": "http://proxy:3128"}, True),
        ({"HTTPS_PROXY": "http://proxy:3128", "NO_PROXY": "hooks.example.com"}, False),
        ({"ALL_PROXY": "http://proxy:3128"}, True),
    ],
)
def test_uses_proxy(monkeypatch, env, expected):
    for var in ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"]:
        monkeypatch.delenv(var, raising=False)
        monkeypatch.delenv(var.lower(), raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    assert alerts_module._uses_proxy(httpx.URL(HOOK)) is expected


# --- API --------------------------------------------------------------------------


@pytest.fixture
def client(db_path):
    with TestClient(app) as c:
        yield c


def test_alert_api_crud_masks_webhook(client, dns):
    client.put("/api/presets/big", json={"min_volume": 100})
    resp = client.put("/api/alerts/big", json={"webhook_url": HOOK})
    assert resp.status_code == 200
    assert resp.json() == {"preset": "big", "webhook": "https://hooks.example.com/…"}

    listed = client.get("/api/alerts").json()
    assert listed[0]["webhook"] == "https://hooks.example.com/…"
    assert "SECRET-TOKEN" not in json.dumps(listed)

    assert client.delete("/api/alerts/big").status_code == 204
    assert client.delete("/api/alerts/big").status_code == 404


def test_alert_api_errors(client, dns):
    assert client.put("/api/alerts/missing", json={"webhook_url": HOOK}).status_code == 404
    client.put("/api/presets/big", json={})
    dns["localhost"] = ["127.0.0.1"]
    resp = client.put("/api/alerts/big", json={"webhook_url": "http://localhost/x"})
    assert resp.status_code == 422
    assert "non-public" in resp.json()["detail"]
    assert client.put("/api/alerts/big", json={}).status_code == 422
    resp = client.put("/api/alerts/big", json={"webhook_url": "https://hooks.example.com:99999/x"})
    assert resp.status_code == 422 and "port" in resp.json()["detail"]


def test_startup_warns_when_auth_is_disabled(db_path, caplog):
    with caplog.at_level("WARNING"), TestClient(app):
        pass
    assert any("AUTH_PASSWORD is not set" in r.message for r in caplog.records)


def test_no_auth_warning_when_password_set(db_path, caplog, monkeypatch):
    monkeypatch.setattr(settings, "auth_password", "secret")
    with caplog.at_level("WARNING"), TestClient(app):
        pass
    assert not any("AUTH_PASSWORD" in r.message for r in caplog.records)


def test_alerts_fragment_empty_state(client):
    resp = client.get("/alerts")
    assert resp.status_code == 200
    assert "No alerts yet" in resp.text


def test_alerts_fragment_lists_alerts_with_masked_webhooks(client, dns):
    client.put("/api/presets/big", json={"min_volume": 100})
    client.put("/api/alerts/big", json={"webhook_url": HOOK})
    resp = client.get("/alerts")
    assert resp.status_code == 200
    assert "https://hooks.example.com/…" in resp.text
    assert "SECRET-TOKEN" not in resp.text
    assert 'data-preset="big"' in resp.text
    assert "failing" not in resp.text


def test_alerts_fragment_flags_failing_alerts(client, db_path, webhook):
    client.put("/api/presets/big", json={"min_volume": 100})
    client.put("/api/alerts/big", json={"webhook_url": HOOK})
    _snapshot(db_path, "t1", [("bitcoin", 500)])
    webhook.status = 500
    check_alerts()
    resp = client.get("/alerts")
    assert "1 alert failing" in resp.text
    assert "Webhook returned HTTP 500" in resp.text


def test_alerts_fragment_escapes_errors(client, db_path, dns):
    client.put("/api/presets/big", json={})
    client.put("/api/alerts/big", json={"webhook_url": HOOK})
    with connect(db_path) as conn:
        conn.execute("UPDATE alerts SET last_error = '<script>x</script>'")
    resp = client.get("/alerts")
    assert "<script>x</script>" not in resp.text
    assert "&lt;script&gt;" in resp.text


def test_dashboard_has_alerts_panel(client):
    resp = client.get("/")
    assert 'id="alerts-panel"' in resp.text
    assert 'hx-get="/alerts"' in resp.text


def test_alert_api_requires_auth(client, dns, monkeypatch):
    monkeypatch.setattr(settings, "auth_password", "secret")
    assert client.get("/api/alerts").status_code == 401
    assert client.get("/alerts").status_code == 401
    assert client.put("/api/alerts/big", json={"webhook_url": HOOK}).status_code == 401
    assert client.delete("/api/alerts/big").status_code == 401


# --- CLI --------------------------------------------------------------------------


def test_alert_cli_flow(db_path, webhook):
    runner = CliRunner()
    _big_volume_preset()

    assert "No alerts yet" in runner.invoke(cli_app, ["alert", "list"]).stdout

    out = runner.invoke(cli_app, ["alert", "set", "big", HOOK])
    assert out.exit_code == 0 and "hooks.example.com/…" in out.stdout
    assert "SECRET-TOKEN" not in out.stdout

    _snapshot(db_path, "t1", [("bitcoin", 500)])
    out = runner.invoke(cli_app, ["alert", "check"])
    assert out.exit_code == 0 and "notified 1 new match(es)" in out.stdout
    out = runner.invoke(cli_app, ["alert", "check"])
    assert "no new matches (1 matching)" in out.stdout

    listed = runner.invoke(cli_app, ["alert", "list"]).stdout
    assert "big" in listed and "SECRET-TOKEN" not in listed

    assert runner.invoke(cli_app, ["alert", "delete", "big"]).exit_code == 0
    assert runner.invoke(cli_app, ["alert", "delete", "big"]).exit_code == 1


def test_alert_cli_errors(db_path, webhook):
    runner = CliRunner()
    out = runner.invoke(cli_app, ["alert", "set", "missing", HOOK])
    assert out.exit_code == 1 and "No preset named 'missing'" in out.stdout

    _big_volume_preset()
    out = runner.invoke(cli_app, ["alert", "set", "big", "not-a-url"])
    assert out.exit_code == 1 and "http://" in out.stdout

    runner.invoke(cli_app, ["alert", "set", "big", HOOK])
    _snapshot(db_path, "t1", [("bitcoin", 500)])
    webhook.status = 500
    out = runner.invoke(cli_app, ["alert", "check"])
    assert out.exit_code == 1 and "delivery failed" in out.stdout


# --- scheduler --------------------------------------------------------------------


def _run_scheduler_once(monkeypatch, fetch_ok: bool, check):
    async def fake_run_fetch():
        if not fetch_ok:
            raise RuntimeError("fetch boom")
        return 1, "t"

    async def stop(_):
        raise asyncio.CancelledError

    monkeypatch.setattr(scheduler, "run_fetch", fake_run_fetch)
    monkeypatch.setattr(scheduler, "check_alerts", check)
    monkeypatch.setattr(scheduler.asyncio, "sleep", stop)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(scheduler.main())


def test_scheduler_checks_alerts_after_successful_fetch(db_path, monkeypatch):
    calls = []
    _run_scheduler_once(monkeypatch, True, lambda: calls.append(1) or [])
    assert calls == [1]


def test_scheduler_skips_alerts_after_failed_fetch(db_path, monkeypatch):
    calls = []
    _run_scheduler_once(monkeypatch, False, lambda: calls.append(1) or [])
    assert calls == []


def test_alert_failure_does_not_stop_the_scheduler(db_path, monkeypatch, caplog):
    def boom():
        raise RuntimeError("alert boom")

    with caplog.at_level("ERROR"):
        _run_scheduler_once(monkeypatch, True, boom)  # reaches sleep -> CancelledError
    assert any("Alert check failed" in r.message for r in caplog.records)
    assert scheduler.heartbeat_path().exists()
