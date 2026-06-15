from datetime import datetime, timezone, timedelta

from flask import Flask

import monitoring as m


def _seed(conn, rows):
    for ts, server, service, status in rows:
        conn.execute(
            "INSERT INTO checks (timestamp, server, service, status, response_time_ms, error_msg) "
            "VALUES (?,?,?,?,?,?)",
            (ts, server, service, status, 10, None),
        )
    conn.commit()


def test_uptime_30d_counts_all_services_not_just_ssh(tmp_path, monkeypatch):
    """uptime_30d must reflect ALL services (ok / (ok+fail)), not only ssh.

    ssh is perfect; nginx is half-failing and also emits a warning. Warnings are
    excluded from numerator and denominator, so the score is 3 ok / 4 (ok+fail)
    = 75.0%. The old ssh-only formula would wrongly report 100.0%.
    """
    db = tmp_path / "mon.db"
    monkeypatch.setattr(m, "DB_PATH", db)
    monkeypatch.setattr(m, "load_config", lambda: {
        "check_interval": 300,
        "servers": {"NODE": {"host": "h", "user": "u", "services": ["nginx"]}},
    })
    m.init_db()

    now = datetime.now(timezone.utc)

    def ts(mins_ago):
        return (now - timedelta(minutes=mins_ago)).isoformat()

    conn = m.get_db()
    _seed(conn, [
        (ts(50), "NODE", "ssh", "ok"),
        (ts(40), "NODE", "ssh", "ok"),
        (ts(30), "NODE", "nginx", "ok"),
        (ts(20), "NODE", "nginx", "fail"),
        (ts(10), "NODE", "nginx", "warning"),  # must NOT count toward the score
    ])
    conn.close()

    app = Flask(__name__)
    with app.app_context():
        data = m.monitoring_status().get_json()

    node = data["servers"]["NODE"]
    assert node["uptime_checks"] == 4   # ok+fail across all services (warning excluded)
    assert node["uptime_ok"] == 3
    assert node["uptime_30d"] == 75.0
