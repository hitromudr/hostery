from datetime import datetime, timezone, timedelta
import monitoring as m

def _ts(now, mins_ago):
    return (now - timedelta(minutes=mins_ago)).isoformat()

def test_severity_curve_bounds():
    assert m._severity_curve(0) == 0.0
    assert m._severity_curve(3600) == 1.0
    assert 0.0 < m._severity_curve(1800) <= 1.0

def test_build_intervals_single_fail_run():
    now = datetime.now(timezone.utc)
    rows = [
        {"timestamp": _ts(now, 30), "status": "ok", "error_msg": None},
        {"timestamp": _ts(now, 25), "status": "fail", "error_msg": "boom"},
        {"timestamp": _ts(now, 20), "status": "ok", "error_msg": None},
    ]
    intervals = m._build_intervals(rows, now)
    assert len(intervals) == 1
    assert intervals[0]["severity"] == "fail"
    assert intervals[0]["ongoing"] is False

def test_build_intervals_ongoing_run():
    now = datetime.now(timezone.utc)
    rows = [
        {"timestamp": _ts(now, 10), "status": "fail", "error_msg": "x"},
        {"timestamp": _ts(now, 5), "status": "fail", "error_msg": "x"},
    ]
    intervals = m._build_intervals(rows, now)
    assert len(intervals) == 1
    assert intervals[0]["ongoing"] is True
    assert intervals[0]["resolved_at"] is None

def test_hourly_timeline_length():
    now = datetime.now(timezone.utc)
    tl = m._hourly_timeline({}, [], now, hours=24)
    assert len(tl) == 24
