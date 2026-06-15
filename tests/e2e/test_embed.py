"""E2E: embed mode (?embed=1) hides the hostery chrome and ?theme= pins theme.

Lets a parent page (AI Cockpit) host the Net View in an iframe without the
sidebar/nav and with a fixed theme.
"""
import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e


def test_embed_hides_sidebar_and_opens_netview(page):
    page.goto("/?embed=1&theme=dark")
    expect(page.locator("#view-monitoring")).to_be_visible()
    expect(page.locator("#main-sidebar")).to_be_hidden()
    assert "theme-light" not in (page.locator("html").get_attribute("class") or "")


def test_no_embed_keeps_sidebar(page):
    page.goto("/")
    expect(page.locator("#main-sidebar")).to_be_visible()
