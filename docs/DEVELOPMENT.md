# Development guide

Architecture, internals, and conventions for contributors. For installation,
running, and test commands see the [README](../README.md).

## Module map

- `app.py` — entry point. Builds the Flask app, wires basic auth, mounts the
  monitoring blueprint and config API, serves the SPA. `start_scheduler()` runs
  the background check loop and is called **only** under `if __name__ ==
  "__main__"`, so importing `app` as a WSGI object does not start SSH checks.
- `monitoring.py` — the core: the SSH check engine (`check_server`), service
  resolution (systemd / port / docker / http / wireguard / custom), SQLite
  persistence (`checks` + `incidents`), the 24h hourly timeline, incident
  tracking, Telegram alerts, and the `/api/monitoring/*` routes.
- `config_api.py` — `/api/config` GET/POST: reads/writes `config.json` with
  token redaction, atomic write, and preservation of unmanaged server fields.
- `auth.py` — HTTP basic auth (`init_auth`), driven by `HOSTERY_AUTH`.
- `sensors.py` — generic host metrics via psutil (CPU, RAM, disk, net, I/O).
- `pi_sensors.py` — optional Raspberry Pi extras (CPU temp, fan, throttling)
  via sysfs; returns zeros off-Pi.
- `templates/index.html` — the single-page app: all CSS (theme variables in
  `:root`), markup for the three views, hash-routed by `showView`.
- `static/js/dashboard.js` — view routing, Dashboard rendering, theme toggle.
- `static/js/monitoring.js` — Net View (server cards, uptime timeline,
  incidents, muted services).
- `static/js/settings.js` — Settings editor (servers, services, custom checks).

## Environment variables

- `HOSTERY_BIND` — bind address (default `127.0.0.1`).
- `HOSTERY_PORT` — port (default `5000`).
- `HOSTERY_CONFIG_DIR` — config directory (default `./config`); `monitoring.db`
  and `config.json` derive from it. Point it at a throwaway dir for demos/tests
  so the real config is untouched.
- `HOSTERY_AUTH` — `user:pass` to fix credentials, unset to auto-generate a
  password (printed in logs), or `off` to disable auth (only behind another
  auth layer).

## Configuration model

`config/config.json` is gitignored (the real fleet); see
`config/config.example.json` for the schema. A server entry has `host`, `user`,
`key` (or `socks` for SOCKS5-only reachability), optional `cockpit_url`, a
`services` list (a plain name = systemd unit, or `{type: port|docker|http|
wireguard}`), and optional `custom_checks` (a shell command + an `expect`
expression + severity). Config is applied live: edits saved via Settings (or in
the file) take effect on the next check cycle without a restart.

## Theme system

Light and dark, both driven by 12 CSS custom properties in `:root`
(`templates/index.html`); `:root.theme-light` overrides them (a Tokyo Night Day
palette). A pre-paint `<head>` script applies `theme-light` to `<html>` from
`localStorage.hostery_theme`, falling back to `prefers-color-scheme` (so there
is no flash). The sidebar `#theme-toggle` (`dashboard.js::toggleTheme`) flips
and persists the choice; with no stored choice the UI follows the OS. The Net
View timeline (`severityColor` in `monitoring.js`) darkens cells on the light
theme for contrast. When adding UI, prefer the existing variables (including
`--on-fill`, `--raise`, `--sink`, `--divider`) over hardcoded colors.

## Regenerating screenshots

`docs/screenshots/*.png` (referenced from the READMEs) are rendered from a
**fictional** fleet on the RFC 5737 documentation IP ranges (`192.0.2.x`,
`198.51.100.x`, `203.0.113.x`) — never real hosts. To regenerate: point
`HOSTERY_CONFIG_DIR` at a temp directory, write a demo `config.json`, seed the
`checks` table with ~24h of history (mostly `ok` with a few `fail` windows),
serve `app` as a WSGI object in a thread (no scheduler), and drive it with
Playwright for both themes.

## Conventions

- Code, identifiers, and comments in **English**.
- New or changed behavior needs a covering test (unit, or E2E for UI flows).
  E2E modules set `pytestmark = pytest.mark.e2e`; the E2E harness backs up and
  restores the local config so it is never lost.
- Prefer the existing CSS variables and follow the surrounding patterns.
- Keep modules focused; `monitoring.py` is the one large module by necessity
  (the check engine + timeline + routes).
