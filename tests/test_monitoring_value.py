"""A custom check keeps the number it printed, not only the verdict.

Until 2026-10-27 `checks` held status and error only: disk-root at 84% and at
12% were the same "ok", and the history could not answer "how fast is it
growing". The `value` column is what turns custom checks into time series.
"""

import sqlite3

from flask import Flask

import monitoring as m


def _fake_ssh(monkeypatch, outputs):
    """Connect always succeeds; each command prints what `outputs` maps it to."""
    monkeypatch.setattr(m, "_get_ssh_client", lambda *a, **k: object())
    monkeypatch.setattr(m, "_close_ssh_client", lambda client: None)

    def exec_(client, cmd, timeout=8):
        out = outputs[cmd]
        if isinstance(out, Exception):
            raise out
        return out

    monkeypatch.setattr(m, "_ssh_exec", exec_)


SERVER = {
    "host": "x",
    "user": "y",
    "services": [],
    "custom_checks": [
        {"name": "mem-avail", "command": "mem", "expect": ">= 10", "warn_if": ">= 5", "severity": "critical"},
        {"name": "swap-used", "command": "swap", "expect": "<= 80", "severity": "warning"},
        {"name": "probe", "command": "text", "expect": "> 0"},
        {"name": "broken", "command": "boom", "expect": "> 0"},
    ],
}


def test_custom_checks_return_the_printed_number(monkeypatch):
    _fake_ssh(monkeypatch, {"mem": "7", "swap": "-3", "text": "listening", "boom": OSError("channel closed")})
    results = {r[0]: r for r in m.check_server("S", SERVER, {"servers": {}})}

    # 7 fails ">= 10" but passes warn_if ">= 5": warning, and the 7 is kept.
    assert results["mem-avail"][1] == "warning"
    assert results["mem-avail"][4] == 7
    assert results["swap-used"][1:2] == ("ok",) and results["swap-used"][4] == -3
    # Non-numeric output has no value to keep.
    assert results["probe"][1] == "ok" and results["probe"][4] is None
    # A check that never ran has no value either.
    assert results["broken"][1] == "warning" and len(results["broken"]) == 4
    # ssh itself carries no value.
    assert len(results["ssh"]) == 4


def _cycle(tmp_path, monkeypatch, results):
    monkeypatch.setattr(m, "DB_PATH", tmp_path / "mon.db")
    monkeypatch.setattr(m, "load_config", lambda: {
        "check_interval": 300,
        "servers": {"S": {"host": "x", "user": "y", "alerts": False,
                          "custom_checks": [{"name": "disk-root", "command": "df"}]}},
    })
    monkeypatch.setattr(m, "check_server", lambda name, cfg, conf: results)
    m.init_db()
    m.run_check_cycle()


def test_cycle_stores_the_value_next_to_the_status(tmp_path, monkeypatch):
    _cycle(tmp_path, monkeypatch, [("ssh", "ok", None, 12), ("disk-root", "ok", None, 30, 84)])
    conn = m.get_db()
    rows = {r["service"]: r["value"] for r in conn.execute("SELECT service, value FROM checks")}
    conn.close()
    assert rows == {"ssh": None, "disk-root": 84}


def test_status_and_history_report_the_value(tmp_path, monkeypatch):
    _cycle(tmp_path, monkeypatch, [("ssh", "ok", None, 12), ("disk-root", "ok", None, 30, 84)])
    app = Flask(__name__)
    with app.test_request_context("/"):
        svc = m.monitoring_status().get_json()["servers"]["S"]["services"]
    with app.test_request_context("/?server=S&service=disk-root"):
        hist = m.monitoring_history().get_json()["checks"]
    with app.test_request_context("/?server=S"):
        hist_all = m.monitoring_history().get_json()["checks"]
    assert svc["disk-root"]["value"] == 84
    assert svc["ssh"]["value"] is None
    assert [c["value"] for c in hist] == [84]
    assert sorted((c["service"], c["value"]) for c in hist_all) == [("disk-root", 84), ("ssh", None)]


def test_init_db_adds_value_to_an_existing_database(tmp_path, monkeypatch):
    # The live database predates the column and holds months of rows: the
    # migration must add it in place and leave the old rows readable.
    db = tmp_path / "mon.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE checks (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL, "
        "server TEXT NOT NULL, service TEXT NOT NULL, status TEXT NOT NULL, "
        "response_time_ms INTEGER, error_msg TEXT, UNIQUE(timestamp, server, service))"
    )
    conn.execute(
        "INSERT INTO checks (timestamp, server, service, status) VALUES ('2026-10-01T00:00:00+00:00', 'S', 'ssh', 'ok')"
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(m, "DB_PATH", db)
    m.init_db()
    m.init_db()  # second start: the column is already there, nothing breaks

    conn = m.get_db()
    rows = [dict(r) for r in conn.execute("SELECT service, status, value FROM checks")]
    conn.close()
    assert rows == [{"service": "ssh", "status": "ok", "value": None}]
