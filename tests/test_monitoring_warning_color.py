"""An hour with warnings only is yellow, not the red-to-brown failure scale.

13.11.2026: `direct` rows (node not reachable without a jump) are warnings by
design and last all hour, so POLKA and JERSEY bars turned brown — the colour of
an hour-long outage — while ssh through the jump was ok the whole time.
"""

from datetime import datetime, timedelta, timezone

import monitoring as m
from test_monitoring_switched_off import _run_js


def _rows(now, statuses):
    """One check every 5 minutes, oldest first, ending one minute ago."""
    n = len(statuses)
    return [
        {"timestamp": (now - timedelta(minutes=1 + 5 * (n - 1 - k))).isoformat(),
         "status": s, "error_msg": None if s == "ok" else "x"}
        for k, s in enumerate(statuses)
    ]


def _last_hour(statuses_by_svc):
    now = datetime.now(timezone.utc).replace(minute=59, second=0, microsecond=0)
    ivs = {svc: m._build_intervals(_rows(now, st), now) for svc, st in statuses_by_svc.items()}
    checks = [now - timedelta(minutes=1)]
    return m._hourly_timeline(ivs, checks, now, hours=24)[-1]


def test_hour_of_warnings_only_is_a_warning_bucket():
    b = _last_hour({"direct": ["warning"] * 12})
    assert b["bad_seconds"] > 3000
    assert b["worst"] == "warning"
    assert b["status"] == "warning"


def test_hour_with_a_fail_keeps_the_fail_scale():
    b = _last_hour({"direct": ["warning"] * 12, "ssh": ["ok"] * 6 + ["fail"] * 6})
    assert b["worst"] == "fail"


def test_long_fail_hour_is_still_fail():
    b = _last_hour({"ssh": ["fail"] * 12})
    assert b["worst"] == "fail"


def test_warning_only_cell_is_drawn_in_the_warning_colour():
    view = _run_js('timelineCellView({status: "warning", worst: "warning", severity: 1, bad_seconds: 3600})')
    assert view == {"cls": "incident", "color": "var(--warning-color)"}


def test_fail_cell_keeps_the_severity_scale():
    view = _run_js('timelineCellView({status: "fail", worst: "fail", severity: 1, bad_seconds: 3600})')
    assert view == {"cls": "incident", "color": _run_js("severityColor(1, 3600)")}
