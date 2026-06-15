"""E2E: Net View card reconcile never duplicates, and a saved drag order is
applied on render.

Native HTML5 drag-and-drop does not simulate reliably through Playwright's
input APIs, so instead of synthesising a drag we assert the two properties the
drag feature actually depends on:
  1. re-rendering from cached status reconciles in place (no duplicate cards) —
     this is the exact regression behind the "задвоилась полка" bug;
  2. the persisted order (localStorage `hostery_server_order`, written by
     saveServerOrder on drop) is honoured by applyServerOrder on the next render.
"""
import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e


def _wait_cards(page):
    page.goto("/?embed=1#monitoring")
    expect(page.locator("#view-monitoring")).to_be_visible()
    expect(page.locator("#mon-servers .server-card").first).to_be_visible(timeout=15000)


def _card_names(page):
    return page.eval_on_selector_all(
        "#mon-servers .server-card", "els => els.map(e => e.dataset.server)"
    )


def test_rerender_does_not_duplicate_cards(page):
    _wait_cards(page)
    before = page.locator("#mon-servers .server-card").count()
    # Force several re-renders from the cached status (exercise the reconcile path).
    page.evaluate("() => { for (let i = 0; i < 3; i++) renderMonServers(monStatusData); }")
    names = _card_names(page)
    assert len(names) == before
    assert len(names) == len(set(names)), f"duplicate cards after re-render: {names}"


def test_saved_order_is_applied(page):
    _wait_cards(page)
    names = _card_names(page)
    assert len(names) >= 2
    reversed_order = list(reversed(names))
    page.evaluate(
        "o => localStorage.setItem('hostery_server_order', JSON.stringify(o))",
        reversed_order,
    )
    page.reload()
    expect(page.locator("#mon-servers .server-card").first).to_be_visible(timeout=15000)
    assert _card_names(page) == reversed_order
