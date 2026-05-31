"""E2E for the light/dark theme toggle.

With no stored preference the page follows the OS (the pre-paint <head> script
handles that). A manual toggle stores an explicit choice that wins over the OS
and survives a reload.
"""
import pytest

pytestmark = pytest.mark.e2e


def test_theme_toggle_persists(page):
    page.goto("/")
    # Clean slate: drop any stored choice and force the dark baseline.
    page.evaluate("localStorage.removeItem('hostery_theme')")
    page.evaluate("document.documentElement.classList.remove('theme-light')")
    page.evaluate("typeof _applyThemeUI === 'function' && _applyThemeUI()")

    is_light = "document.documentElement.classList.contains('theme-light')"
    assert page.evaluate(is_light) is False

    # Toggle on -> light, label + stored choice updated.
    page.click("#theme-toggle")
    assert page.evaluate(is_light) is True
    assert page.evaluate("localStorage.getItem('hostery_theme')") == "light"
    assert page.inner_text("#theme-toggle-label").strip().lower() == "light"

    # Choice persists across reload.
    page.reload()
    assert page.evaluate(is_light) is True

    # Toggle off -> dark, stored choice flips.
    page.click("#theme-toggle")
    assert page.evaluate(is_light) is False
    assert page.evaluate("localStorage.getItem('hostery_theme')") == "dark"
