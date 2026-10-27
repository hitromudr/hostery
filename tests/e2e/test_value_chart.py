"""E2E: a custom check's stored values draw as a 24h line in its detail panel."""
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from playwright.sync_api import expect


pytestmark = pytest.mark.e2e

# The database the e2e harness starts the app against (see conftest.py).
DB_PATH = Path(__file__).resolve().parents[2] / "config" / "monitoring.db"


def test_sparkline_scales_to_the_values(page):
    page.goto("/#monitoring")
    now = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)
    # History arrives newest first, as the API returns it.
    history = [
        {"timestamp": (now - timedelta(minutes=10 * i)).isoformat(), "value": v}
        for i, v in enumerate([84, 70, 77])
    ] + [{"timestamp": (now - timedelta(minutes=40)).isoformat(), "value": None}]
    out = page.evaluate(
        """(h) => { const d = document.createElement('div'); d.innerHTML = valueSparkline(h);
                    return { range: d.querySelector('.svc-value-range').textContent.trim(),
                             points: d.querySelector('polyline').getAttribute('points') }; }""",
        history,
    )
    assert out["range"] == "min 70 · max 84 · now 84"
    # Oldest (77) at x=0, newest (84) at x=300; max sits at the top (y=2),
    # min at the bottom (y=38).
    assert out["points"] == "0.0,20.0 150.0,38.0 300.0,2.0"


def test_one_value_is_not_a_trend(page):
    page.goto("/#monitoring")
    html = page.evaluate(
        "() => valueSparkline([{timestamp: '2026-10-27T12:00:00+00:00', value: 5},"
        " {timestamp: '2026-10-27T11:50:00+00:00', value: null}])"
    )
    assert html == ""


def test_detail_panel_shows_the_stored_values(page):
    # The startup cycle has already failed against 127.0.0.1; seed what a
    # healthy custom check would have stored over the last half hour.
    now = datetime.now(timezone.utc)
    conn = sqlite3.connect(DB_PATH)
    for i, v in enumerate([42, 40, 41]):
        conn.execute(
            "INSERT OR REPLACE INTO checks (timestamp, server, service, status, response_time_ms, error_msg, value) "
            "VALUES (?, 'web-01', 'disk-free', 'ok', 5, NULL, ?)",
            ((now - timedelta(minutes=10 * i, seconds=1)).isoformat(), v),
        )
    conn.commit()
    conn.close()

    page.goto("/#monitoring")
    page.evaluate("() => localStorage.removeItem('mon-compact')")
    page.reload()
    card = page.locator('#mon-servers .server-card[data-server="web-01"]')
    expect(card).to_be_visible(timeout=15000)
    item = card.locator(".svc-item", has=page.locator(".svc-name", has_text="disk-free"))
    assert "value 42" in item.get_attribute("title")
    item.click()
    expect(page.locator("#svc-detail-web-01 .svc-value-range")).to_have_text(
        "min 40 · max 42 · now 42", timeout=10000
    )
