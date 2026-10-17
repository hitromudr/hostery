"""E2E: in the compact Net View a click on a card's header opens that one card
to full, a second click returns it to compact, and the choice survives reload.
"""
import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e


def _open_compact(page):
    page.goto("/?embed=1#monitoring")
    page.evaluate("() => { localStorage.setItem('mon-compact', '1');"
                  " localStorage.removeItem('hostery_expanded_servers'); }")
    page.reload()
    expect(page.locator("#mon-servers .server-card").first).to_be_visible(timeout=15000)


def _card(page, name):
    return page.locator(f'#mon-servers .server-card[data-server="{name}"]')


def test_header_click_expands_one_card(page):
    _open_compact(page)
    web, vpn = _card(page, "web-01"), _card(page, "vpn-01")
    assert "compact" in web.get_attribute("class")
    expect(web.locator(".server-stats")).to_be_hidden()

    web.locator(".server-name").click()
    assert "compact" not in web.get_attribute("class")
    expect(web.locator(".server-stats")).to_be_visible()
    # The other card stays compact.
    assert "compact" in vpn.get_attribute("class")

    page.reload()
    expect(web).to_be_visible(timeout=15000)
    assert "compact" not in web.get_attribute("class")

    web.locator(".server-name").click()
    assert "compact" in web.get_attribute("class")
    expect(web.locator(".server-stats")).to_be_hidden()


def test_header_click_does_nothing_in_full_view(page):
    page.goto("/?embed=1#monitoring")
    page.evaluate("() => { localStorage.removeItem('mon-compact');"
                  " localStorage.removeItem('hostery_expanded_servers'); }")
    page.reload()
    web = _card(page, "web-01")
    expect(web).to_be_visible(timeout=15000)
    web.locator(".server-name").click()
    assert "compact" not in web.get_attribute("class")
    assert page.evaluate("() => localStorage.getItem('hostery_expanded_servers')") is None
