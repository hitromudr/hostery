# CLAUDE.md

Guidance for Claude Code (claude.ai/code) when working in this repository.

## Overview

**hostery** is a small, self-hostable server dashboard: a single-page web UI
with three views — **Dashboard** (live host metrics), **Net View**
(SSH-based monitoring of a fleet with a 24h uptime timeline + incidents), and
**Settings** (edit the fleet config from the browser). It is a generic,
public fork of the Dashboard + Net View extracted from the private "AI Cockpit"
on the RPi5.

Backend: Flask + psutil + paramiko (SSH) + SQLite. No external services.

## Architecture

- `app.py` — entry point. Builds the Flask app, wires basic auth, mounts the
  monitoring blueprint and config API, serves the SPA. `start_scheduler()` runs
  the background check loop, and is called **only** under `if __name__ ==
  "__main__"` (so importing `app` as a WSGI object does not start SSH checks).
- `monitoring.py` — the core. SSH check engine (`check_server`), service
  resolution (systemd/port/docker/http/wireguard/custom), SQLite persistence
  (`checks` + `incidents` tables), the 24h hourly timeline computation
  (`_hourly_timeline`/`severityColor` mirror), incident tracking, Telegram
  alerts, and the `/api/monitoring/*` routes. `CONFIG_DIR`/`DB_PATH`/
  `CONFIG_PATH` derive from `HOSTERY_CONFIG_DIR` (default `./config`).
- `config_api.py` — `/api/config` GET/POST: reads/writes `config.json`
  (token redaction, atomic write, preserves unmanaged server fields).
- `auth.py` — HTTP basic auth (`init_auth`). Driven by `HOSTERY_AUTH`.
- `sensors.py` — generic host metrics via psutil (CPU, RAM, disk, net, I/O).
- `pi_sensors.py` — optional Raspberry Pi extras (CPU temp, fan, throttling)
  via sysfs; returns zeros off-Pi.
- `templates/index.html` — the SPA: all CSS (theme variables in `:root`),
  markup for the three views, hash-routed via `showView` in `dashboard.js`.
- `static/js/dashboard.js` — view routing, Dashboard rendering, theme toggle.
- `static/js/monitoring.js` — Net View rendering (server cards, timeline,
  incidents, muted services).
- `static/js/settings.js` — Settings editor (servers, services, custom checks).

## Running locally

```bash
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
HOSTERY_AUTH=admin:pw ./venv/bin/python app.py   # serves http://127.0.0.1:5000
```

Env vars: `HOSTERY_BIND` (default `127.0.0.1`), `HOSTERY_PORT` (default `5000`),
`HOSTERY_CONFIG_DIR` (default `./config`), `HOSTERY_AUTH` — `user:pass` to fix
credentials, unset to auto-generate a password (printed in logs), or `off` to
disable auth (only behind another auth layer).

Docker: `docker compose up --build` (binds `127.0.0.1:5000`, mounts `./config`
and `~/.ssh` read-only). Put a TLS reverse proxy in front for remote access.

## Configuration

`config/config.json` is gitignored (holds the real fleet); see
`config/config.example.json` for the schema. A server entry has `host`, `user`,
`key` (or `socks` for SOCKS5-only reachability), optional `cockpit_url`, a
`services` list (plain name = systemd, or `{type: port|docker|http|wireguard}`),
and optional `custom_checks` (shell command + `expect` expression + severity).

## Tests

```bash
./venv/bin/python -m pytest                 # unit tests only (addopts: -m "not e2e")
./venv/bin/pip install -r requirements-dev.txt && ./venv/bin/playwright install chromium
./venv/bin/python -m pytest tests/e2e -m e2e   # Playwright E2E (launches the app)
```

E2E is **safe**: `tests/e2e/conftest.py` backs up `config/config.json` and
`monitoring.db` to `*.e2ebak` before the run and restores them in teardown, so
the local fleet config is never lost. New/changed behavior needs a covering
test (project rule). E2E modules set `pytestmark = pytest.mark.e2e`.

## Theme system

Light + dark, both driven by 12 CSS custom properties in `:root`
(`templates/index.html`); `:root.theme-light` overrides them (Tokyo Night Day).
A pre-paint `<head>` script applies `theme-light` to `<html>` from
`localStorage.hostery_theme`, falling back to `prefers-color-scheme` (avoids a
flash). The sidebar `#theme-toggle` (in `dashboard.js::toggleTheme`) flips and
persists the choice; with no stored choice the UI follows the OS. The Net View
timeline (`severityColor` in `monitoring.js`) darkens cells on the light theme
for contrast. When adding UI, prefer the existing CSS variables (incl.
`--on-fill`, `--raise`, `--sink`, `--divider`) over hardcoded colors.

## Screenshots

`docs/screenshots/*.png` (referenced from both READMEs) are rendered from a
fictional fleet on RFC 5737 documentation IP ranges — never real hosts. To
regenerate: spin an isolated instance with `HOSTERY_CONFIG_DIR` pointing at a
temp dir, seed the `checks` table with ~24h of history, run the app as a WSGI
object in a thread (no scheduler), and drive it with Playwright for both themes.

## Deployment & git

This local clone has **no git remotes**. Commits are pushed to **Forgejo
(primary)** via the RPi staging host; **GitHub** is a push-mirror that updates
automatically. Commit on a branch (default branch is `master`), in Russian, and
only when the user asks.

## Conventions

- Communication with the user: **Russian**. Code, identifiers, comments,
  CLAUDE.md, and docs: **English**.
- Neutral, technical tone (see workspace `.rules`).
- Read before write; report all errors; no destructive actions without explicit
  permission.
