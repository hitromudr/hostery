# Light theme + README screenshots — design

Date: 2026-05-31

## Goal

Two user-facing additions to **hostery**:

1. A **light theme** alongside the existing dark (Tokyo Night) theme.
2. **Screenshots** in `README.md` / `README.en.md`, rendered from a realistic
   but fully fictional fleet (no real servers/IPs).

## 1. Light theme

### Approach
All colors already flow through 12 CSS custom properties in `:root`
(`templates/index.html`). Add a `body.theme-light { ... }` block overriding
those 12 vars with a Tokyo Night Day palette.

| var | dark | light |
|---|---|---|
| --bg-color | #1a1b26 | #e1e2e7 |
| --sidebar-bg | #16161e | #d0d1d9 |
| --card-bg | #24283b | #ffffff |
| --text-color | #c0caf5 | #343b58 |
| --text-muted | #565f89 | #6c6f93 |
| --accent-color | #7aa2f7 | #34548a |
| --success-color | #9ece6a | #587539 |
| --warning-color | #e0af68 | #8f5e15 |
| --danger-color | #f7768e | #c64343 |
| --border-color | #414868 | #c4c8da |
| --progress-bg | #414868 | #c4c8da |
| --progress-fill | #7aa2f7 | #34548a |

### Mode logic (auto + manual)
- An inline script in `<head>` runs before paint: if `localStorage.hostery_theme`
  is set (`light`/`dark`), apply it; otherwise follow `prefers-color-scheme`.
  Prevents flash of wrong theme.
- A sun/moon toggle in the sidebar calls `toggleTheme()`, which flips
  `body.classList` and stores the explicit choice in `localStorage`.
- With no stored choice, a `matchMedia('(prefers-color-scheme)')` listener keeps
  the UI in sync with the OS. Once the user picks manually, the manual choice wins.

### Hardcoded-color audit
Most JS uses `var(--...)`. The template has overlay colors that assume a dark
surface and must become theme-aware:
- `rgba(255,255,255,0.0X)` raise-overlays → a `--overlay-raise` var
  (white-on-dark, black-on-light).
- `rgba(0,0,0,0.X)` sink-overlays → a `--overlay-sink` var.
- `rgba(247,118,142,...)` / `rgba(224,175,104,...)` / `rgba(158,206,106,...)`
  tinted backgrounds → keep hue, acceptable on both; revisit only if unreadable.
- `#1a1b26` / `#fff` used as text **on** a colored fill (badges, buttons) stay —
  they are foreground-on-accent, valid in both themes.

### Test
E2E: toggle switches `body` class and the choice survives a reload.

## 2. Screenshots + mocks (one-off, generator not committed)

- Fictional fleet on documentation IP ranges (RFC 5737):
  `192.0.2.x`, `198.51.100.x`, `203.0.113.x`. Neutral names:
  `node-fra-01`, `edge-ams-01`, `db-waw-01`, `vpn-hel-01`, …
- Seed `monitoring.db` with ~24h of `checks` rows per server/service
  (mostly `ok`, a few `fail` windows, one active incident + resolved history)
  so timelines look populated like the reference.
- Run against an **isolated config dir** so the developer's real
  `config/config.json` and `monitoring.db` are never touched. Add a
  `HOSTERY_CONFIG_DIR` env override in `monitoring.py` (`DB_PATH`/`CONFIG_PATH`
  derive from it; defaults unchanged). Generally useful, keeps real data safe.
- Playwright captures 6 PNGs (Dashboard, Net View, Settings × dark/light) into
  `docs/screenshots/`. The generation is one-off; only the PNGs are committed.

## 3. README

Embed the screenshots in both `README.md` and `README.en.md`.

## Out of scope
- Per-component theming beyond the 12 vars + overlay vars.
- Committing a reproducible screenshot generator (chosen: one-off).
