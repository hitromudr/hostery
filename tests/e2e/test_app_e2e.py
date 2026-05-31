"""End-to-end coverage of the hostery SPA via Playwright.

Scenarios:
  1. auth gate blocks unauthenticated access
  2. dashboard loads, host metrics populate, Power Health hidden off-Pi
  3. navigation across Dashboard / Net View / Settings
  4. Net View renders fleet server cards + a Cockpit control slot per server
  5. Settings loads the live config with the telegram token redacted
  6. Settings: add a server and persist it (Save -> reload)
  7. Settings validation surfaces errors (missing host)
  8. Settings: add a service row to a server
  9. Test connection reports an SSH failure against the fake fleet
 10. Cockpit control renders/degrades gracefully against the fake fleet
"""
import re

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e


def _open_settings(page):
    page.goto("/")
    page.click("#nav-settings")
    expect(page.locator("#settings-root .card").first).to_be_visible(timeout=10000)


# 1 -------------------------------------------------------------------------
def test_unauthenticated_blocked(playwright, hostery_server):
    rc = playwright.request.new_context()
    r = rc.get(hostery_server["base_url"] + "/api/stats")
    assert r.status == 401
    rc.dispose()


# 2 -------------------------------------------------------------------------
def test_dashboard_loads_and_metrics(page):
    page.goto("/")
    expect(page.locator("#nav-dashboard")).to_have_class(re.compile(r"\bactive\b"))
    expect(page.get_by_text("System Overview")).to_be_visible()
    expect(page.locator("#ram-pct")).not_to_have_text("--", timeout=15000)
    expect(page.locator("#disk-pct")).not_to_have_text("--", timeout=15000)
    expect(page.locator("#clock")).not_to_have_text("--:--", timeout=15000)
    # No vcgencmd on the test host -> Power Health stays hidden.
    expect(page.locator("#power-section")).to_be_hidden()


# 3 -------------------------------------------------------------------------
def test_navigation_between_views(page):
    page.goto("/")
    page.click("#nav-monitoring")
    expect(page.locator("#view-monitoring")).to_be_visible()
    page.click("#nav-settings")
    expect(page.locator("#view-settings")).to_be_visible()
    expect(page.locator("#settings-root .card").first).to_be_visible(timeout=10000)
    page.click("#nav-dashboard")
    expect(page.locator("#view-dashboard")).to_be_visible()


# 4 -------------------------------------------------------------------------
def test_net_view_renders_servers_and_cockpit_slot(page):
    page.goto("/")
    page.click("#nav-monitoring")
    expect(page.locator("#view-monitoring")).to_be_visible()
    expect(page.get_by_text("web-01").first).to_be_visible(timeout=15000)
    expect(page.get_by_text("vpn-01").first).to_be_visible(timeout=15000)
    expect(page.locator("#cockpit-web-01")).to_have_count(1)
    expect(page.locator("#cockpit-vpn-01")).to_have_count(1)


# 5 -------------------------------------------------------------------------
def test_settings_loads_with_redacted_token(page):
    _open_settings(page)
    expect(page.locator("#cfg-tg-token")).to_have_value("")        # token never sent to browser
    expect(page.locator("#cfg-tg-chat")).to_have_value("55555")
    expect(page.locator('input.srv-name[value="web-01"]')).to_have_count(1)
    expect(page.locator('input.srv-name[value="vpn-01"]')).to_have_count(1)


# 6 -------------------------------------------------------------------------
def test_settings_add_server_and_persist(page):
    _open_settings(page)
    page.get_by_role("button", name=re.compile("Add server")).click()
    new_card = page.locator('#cfg-servers .card').filter(
        has=page.locator('input.srv-name[value="new-server"]'))
    expect(new_card).to_have_count(1)
    new_card.locator(".srv-host").fill("10.0.0.99")
    page.get_by_role("button", name=re.compile(r"^\s*Save")).click()
    expect(page.locator("#cfg-msg")).to_have_text("Saved.", timeout=10000)
    # Persisted: reload reads it back from config.json.
    page.reload()
    page.click("#nav-settings")
    expect(page.locator("#settings-root .card").first).to_be_visible(timeout=10000)
    expect(page.locator('input.srv-name[value="new-server"]')).to_have_count(1)


# 7 -------------------------------------------------------------------------
def test_settings_validation_missing_host(page):
    _open_settings(page)
    page.get_by_role("button", name=re.compile("Add server")).click()  # new-server has empty host
    page.get_by_role("button", name=re.compile(r"^\s*Save")).click()
    expect(page.locator("#cfg-msg")).to_contain_text("missing host", timeout=10000)


# 8 -------------------------------------------------------------------------
def test_settings_add_service_row(page):
    _open_settings(page)
    card = page.locator('#cfg-servers .card').filter(
        has=page.locator('input.srv-name[value="web-01"]'))
    before = card.locator(".svc-row").count()
    card.get_by_role("button", name="+ service").click()
    expect(card.locator(".svc-row")).to_have_count(before + 1)


# 9 -------------------------------------------------------------------------
def test_test_connection_reports_failure(page):
    _open_settings(page)
    card = page.locator('#cfg-servers .card').filter(
        has=page.locator('input.srv-name[value="web-01"]'))
    card.get_by_role("button", name=re.compile("Test connection")).click()
    # SSH to 127.0.0.1 as root with this key fails fast -> the row shows a ✗ result.
    expect(card.locator(".ssh-result")).to_contain_text("✗", timeout=20000)


# 10 ------------------------------------------------------------------------
def test_cockpit_control_renders(page):
    page.goto("/")
    page.click("#nav-monitoring")
    slot = page.locator("#cockpit-web-01")
    expect(slot).to_have_count(1)
    # loadCockpit SSHes once; against the fake fleet it resolves to an "unknown"
    # status chip (or an Install/console control on a real host) — never crashes.
    expect(slot).to_contain_text(re.compile("Cockpit|Install|console|installed"), timeout=20000)
