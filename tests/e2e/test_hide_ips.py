"""E2E: the eye button in the Net View header hides server IPs for screenshots.

Addresses (and configured hostnames) in text are wrapped into .ip-redact; the
`hide-ips` class on <html> paints them as bars. Titles carrying an address are
masked while hidden and restored on show. The choice survives a reload.
"""
import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e

HIDDEN = "document.documentElement.classList.contains('hide-ips')"


def _open(page):
    page.goto("/#monitoring")
    page.evaluate("localStorage.removeItem('hostery_hide_ips')")
    page.reload()
    expect(page.locator("#mon-servers .server-card").first).to_be_visible(timeout=15000)


def test_eye_hides_and_restores_ips(page):
    _open(page)
    btn = page.locator("#ip-toggle-btn")
    expect(btn).to_be_visible()
    assert page.evaluate(HIDDEN) is False

    ip = page.locator('.server-card[data-server="web-01"] .server-ip .ip-redact')
    expect(ip).to_have_text("127.0.0.1")
    assert page.evaluate(
        "getComputedStyle(document.querySelector('.server-ip .ip-redact')).color"
    ) != "rgba(0, 0, 0, 0)"

    btn.click()
    assert page.evaluate(HIDDEN) is True
    assert page.evaluate("localStorage.getItem('hostery_hide_ips')") == "1"
    assert page.evaluate(
        "getComputedStyle(document.querySelector('.server-ip .ip-redact')).color"
    ) == "rgba(0, 0, 0, 0)"
    expect(btn.locator("i")).to_have_class("fas fa-eye-slash")

    # Survives reload, applied before paint by the <head> script.
    page.reload()
    assert page.evaluate(HIDDEN) is True

    page.locator("#ip-toggle-btn").click()
    assert page.evaluate(HIDDEN) is False
    assert page.evaluate("localStorage.getItem('hostery_hide_ips')") == ""


def test_dynamic_text_and_titles_are_redacted(page):
    _open(page)
    page.locator("#ip-toggle-btn").click()
    page.evaluate("hosteryPrivacy.registerHosts(['db.internal.example'])")
    page.evaluate("""() => {
        const d = document.createElement('div');
        d.id = 'probe';
        d.title = 'ssh to 10.1.2.3 failed';
        d.textContent = 'connect to db.internal.example port 22 via 10.1.2.3; mainland ok';
        document.getElementById('mon-servers').appendChild(d);
    }""")
    probe = page.locator("#probe")
    expect(probe.locator(".ip-redact")).to_have_count(2)
    # A hostname must not match inside other words.
    assert "mainland ok" in probe.inner_text()
    assert probe.get_attribute("title") == "ssh to ••• failed"

    page.locator("#ip-toggle-btn").click()
    assert probe.get_attribute("title") == "ssh to 10.1.2.3 failed"
