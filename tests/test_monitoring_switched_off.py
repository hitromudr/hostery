"""Nodes with `switched_off_ok`: an unreachable node is "off", not an outage.

A workstation the owner powers down at will must not fill the history with
red "down" that has no event behind it. These tests pin the exact values:
the stored status, the incident table, the alert calls, the timeline bucket
status, the uptime numbers and the cell the browser draws.
"""
import errno
import json
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import paramiko
import pytest
import socks
from flask import Flask

import config_api
import monitoring as m

ROOT = Path(__file__).resolve().parent.parent


# --- classification: did the node itself not answer? ---


@pytest.mark.parametrize("exc", [
    TimeoutError("timed out"),
    ConnectionRefusedError(errno.ECONNREFUSED, "Connection refused"),
    ConnectionResetError(errno.ECONNRESET, "Connection reset by peer"),
    OSError(errno.EHOSTUNREACH, "No route to host"),
    OSError(errno.ENETUNREACH, "Network is unreachable"),
    paramiko.ssh_exception.NoValidConnectionsError(
        {("127.0.0.1", 2203): ConnectionRefusedError(errno.ECONNREFUSED, "refused")}),
    # A loopback forward (socat/haproxy) accepts, finds nothing behind, closes.
    paramiko.SSHException("Error reading SSH protocol banner"),
    EOFError(),
    # Through a jump host: the jump answered, the target did not.
    paramiko.ChannelException(2, "Connect failed"),
    paramiko.SSHException("Timeout opening channel."),
    # Through SOCKS: the proxy answered, the target did not.
    socks.SOCKS5Error("0x04: Host unreachable"),
    socks.SOCKS5Error("0x05: Connection refused"),
    socks.GeneralProxyError("Socket error", TimeoutError("timed out")),
])
def test_node_did_not_answer(exc):
    assert m.node_did_not_answer(exc) is True


@pytest.mark.parametrize("exc", [
    # The node answered: a wrong key is a real problem, not "switched off".
    paramiko.AuthenticationException("Authentication failed."),
    paramiko.SSHException("Negotiation failed."),
    FileNotFoundError(errno.ENOENT, "No such file", "/nonexistent/key"),
    paramiko.ChannelException(1, "Administratively prohibited"),
    # The path failed, not the node.
    m.JumpHostError("jump host HITRO: timed out"),
    socks.ProxyConnectionError("Error connecting to SOCKS5 proxy 127.0.0.1:1080",
                               ConnectionRefusedError(errno.ECONNREFUSED, "refused")),
    socks.SOCKS5Error("0x02: Connection not allowed by ruleset"),
    ValueError("something else"),
])
def test_node_answered_or_path_failed(exc):
    assert m.node_did_not_answer(exc) is False


def test_switched_off_ok_defaults_to_false():
    assert m.switched_off_ok({"host": "h"}) is False
    assert m.switched_off_ok({"host": "h", "switched_off_ok": True}) is True


# --- check_server ---


def _check(monkeypatch, exc, server_cfg):
    def boom(*a, **k):
        raise exc
    monkeypatch.setattr(m, "_get_ssh_client", boom)
    monkeypatch.setattr(m.time, "sleep", lambda *_: None)
    return m.check_server("LAPTOP", server_cfg, {"ssh_timeout": 1, "servers": {}})


LAPTOP = {
    "host": "127.0.0.1", "ssh_port": 2203, "user": "u", "switched_off_ok": True,
    "services": ["rnsd", {"name": "web", "type": "port", "port": 80}],
    "custom_checks": [{"name": "disk", "command": "echo 1", "expect": "> 0"}],
}


def test_flagged_node_that_did_not_answer_is_off_in_every_check(monkeypatch):
    results = _check(monkeypatch, TimeoutError("timed out"), LAPTOP)
    assert [(r[0], r[1]) for r in results] == [
        ("ssh", "off"), ("rnsd", "off"), ("web", "off"), ("disk", "off"),
    ]
    assert results[0][2] == "timed out"  # the reason stays visible


def test_unflagged_node_that_did_not_answer_is_still_down(monkeypatch):
    cfg = dict(LAPTOP, switched_off_ok=False)
    results = _check(monkeypatch, TimeoutError("timed out"), cfg)
    assert [(r[0], r[1]) for r in results] == [
        ("ssh", "fail"), ("rnsd", "unknown"), ("web", "unknown"),
    ]


def test_flagged_node_that_rejects_the_key_is_down(monkeypatch):
    results = _check(monkeypatch, paramiko.AuthenticationException("Authentication failed."), LAPTOP)
    assert results[0][:2] == ("ssh", "fail")


def test_dead_jump_host_is_not_read_as_target_switched_off(monkeypatch):
    """The jump host's own connect failure is tagged, so the target stays "fail"."""
    class FakeClient:
        def set_missing_host_key_policy(self, policy):
            pass

        def connect(self, **kwargs):
            raise TimeoutError("timed out")

    monkeypatch.setattr(m.paramiko, "SSHClient", FakeClient)
    monkeypatch.setattr(m.time, "sleep", lambda *_: None)
    config = {"ssh_timeout": 1, "servers": {"BASTION": {"host": "b", "user": "u"}}}
    cfg = dict(LAPTOP, jump="BASTION")
    with pytest.raises(m.JumpHostError):
        m._get_ssh_client("LAPTOP", cfg, config)
    results = m.check_server("LAPTOP", cfg, config)
    assert results[0][:2] == ("ssh", "fail")


# --- full cycle: checks, incidents, alerts ---


def _cycle(tmp_path, monkeypatch, config, results):
    monkeypatch.setattr(m, "DB_PATH", tmp_path / "mon.db")
    monkeypatch.setattr(m, "load_config", lambda: config)
    sent = []
    monkeypatch.setattr(m, "send_telegram", lambda cfg, msg: sent.append(msg))
    monkeypatch.setattr(m, "check_server", lambda name, cfg, conf: results)
    m.init_db()
    return sent


def _rows(sql, *args):
    conn = m.get_db()
    try:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


CYCLE_CONFIG = {
    "check_interval": 300,
    # alerts left at the default (on): "off" must stay silent by itself.
    "servers": {"LAPTOP": {"host": "127.0.0.1", "switched_off_ok": True, "services": ["rnsd"]}},
}


def test_off_cycle_records_off_opens_nothing_and_stays_silent(tmp_path, monkeypatch):
    sent = _cycle(tmp_path, monkeypatch, CYCLE_CONFIG,
                  [("ssh", "off", "timed out", 0), ("rnsd", "off", None, 0)])
    m.run_check_cycle()
    checks = _rows("SELECT service, status FROM checks WHERE server='LAPTOP' ORDER BY service")
    assert checks == [{"service": "rnsd", "status": "off"}, {"service": "ssh", "status": "off"}]
    assert _rows("SELECT * FROM incidents") == []
    assert sent == []


def test_going_off_closes_an_open_incident_without_a_recovered_alert(tmp_path, monkeypatch):
    """An incident left open while the node is off would sit red for days."""
    sent = _cycle(tmp_path, monkeypatch, CYCLE_CONFIG,
                  [("ssh", "off", "timed out", 0), ("rnsd", "off", None, 0)])
    conn = m.get_db()
    conn.execute("INSERT INTO incidents (server, service, started_at, error_msg) "
                 "VALUES ('LAPTOP', 'rnsd', '2026-10-14T08:00:00+00:00', 'Service: failed')")
    conn.commit()
    conn.close()

    m.run_check_cycle()

    inc = _rows("SELECT service, resolved_at FROM incidents")
    assert len(inc) == 1 and inc[0]["service"] == "rnsd"
    assert inc[0]["resolved_at"] == m._last_check_time
    assert sent == []


def test_update_incidents_off_returns_switched_off_not_resolved(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "DB_PATH", tmp_path / "mon.db")
    m.init_db()
    conn = m.get_db()
    assert m.update_incidents(conn, "L", "ssh", "off", "2026-10-14T09:00:00+00:00") is None
    conn.execute("INSERT INTO incidents (server, service, started_at) "
                 "VALUES ('L', 'ssh', '2026-10-14T08:00:00+00:00')")
    assert m.update_incidents(conn, "L", "ssh", "off", "2026-10-14T09:00:00+00:00") == "switched_off"
    conn.close()


def test_flagged_node_that_answers_reports_a_failed_service_as_usual(tmp_path, monkeypatch):
    sent = _cycle(tmp_path, monkeypatch, CYCLE_CONFIG,
                  [("ssh", "ok", None, 5), ("rnsd", "fail", "Service: failed", 3)])
    m.run_check_cycle()
    inc = _rows("SELECT service, resolved_at FROM incidents")
    assert inc == [{"service": "rnsd", "resolved_at": None}]
    assert len(sent) == 1 and "rnsd" in sent[0] and "DOWN" in sent[0]


# --- timeline ---


def test_build_intervals_off_ends_a_fail_run():
    now = datetime.now(timezone.utc)
    rows = [
        {"timestamp": (now - timedelta(minutes=30)).isoformat(), "status": "fail", "error_msg": "x"},
        {"timestamp": (now - timedelta(minutes=20)).isoformat(), "status": "off", "error_msg": "timed out"},
    ]
    ivs = m._build_intervals(rows, now)
    assert len(ivs) == 1
    assert ivs[0]["ongoing"] is False
    assert ivs[0]["resolved_at"] == rows[1]["timestamp"]


def test_hour_with_only_off_checks_is_off():
    now = datetime.now(timezone.utc)
    tl = m._hourly_timeline({}, [], now, hours=24,
                            off_timestamps=[now - timedelta(minutes=1)])
    assert tl[-1]["status"] == "off"
    assert tl[-1]["severity"] == 0.0 and tl[-1]["bad_seconds"] == 0
    assert tl[-2]["status"] == "empty"


def test_hour_with_off_and_ok_checks_is_ok():
    now = datetime.now(timezone.utc)
    tl = m._hourly_timeline({}, [now - timedelta(minutes=2)], now, hours=24,
                            off_timestamps=[now - timedelta(minutes=1)])
    assert tl[-1]["status"] == "ok"


def test_hour_with_off_and_a_fail_run_shows_the_fail():
    """Mixed hour: the worst real state wins; "off" does not hide a failure."""
    now = datetime(2026, 10, 14, 10, 59, tzinfo=timezone.utc)
    rows = [
        {"timestamp": "2026-10-14T10:00:00+00:00", "status": "fail", "error_msg": "x"},
        {"timestamp": "2026-10-14T10:50:00+00:00", "status": "off", "error_msg": "timed out"},
    ]
    ivs = m._build_intervals(rows, now)
    real, off = m._split_check_timestamps(rows)
    tl = m._hourly_timeline({"ssh": ivs}, real, now, hours=24, off_timestamps=off)
    assert tl[-1]["status"] == "warning"  # 50 min bad, severity < 1
    assert tl[-1]["bad_seconds"] == 3000


# --- API: card status and uptime ---


def _seed(conn, rows):
    for ts, service, status in rows:
        conn.execute(
            "INSERT INTO checks (timestamp, server, service, status, response_time_ms, error_msg) "
            "VALUES (?,?,?,?,?,?)",
            (ts, "LAPTOP", service, status, 10, None),
        )
    conn.commit()


def test_status_api_reports_off_and_keeps_off_out_of_uptime(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "DB_PATH", tmp_path / "mon.db")
    monkeypatch.setattr(m, "load_config", lambda: {
        "servers": {"LAPTOP": {"host": "127.0.0.1", "user": "u", "switched_off_ok": True,
                               "services": ["rnsd"]}},
    })
    m.init_db()
    now = datetime.now(timezone.utc)

    def ts(mins_ago):
        return (now - timedelta(minutes=mins_ago)).isoformat()

    conn = m.get_db()
    _seed(conn, [
        (ts(300), "ssh", "ok"), (ts(300), "rnsd", "ok"),
        (ts(2), "ssh", "off"), (ts(2), "rnsd", "off"),
        (ts(1), "ssh", "off"), (ts(1), "rnsd", "off"),
    ])
    conn.close()

    app = Flask(__name__)
    with app.test_request_context("/"):
        srv = m.monitoring_status().get_json()["servers"]["LAPTOP"]
        uptime = m.monitoring_uptime().get_json()["uptime"]["LAPTOP"]

    assert srv["status"] == "off"
    assert srv["services"]["ssh"]["status"] == "off"
    assert srv["services"]["rnsd"]["status"] == "off"
    # Off is out of both numerator and denominator: 2 ok of 2 counted.
    assert srv["uptime_30d"] == 100.0
    assert srv["uptime_checks"] == 2 and srv["uptime_ok"] == 2
    assert uptime == 100.0
    assert srv["uptime_timeline"][-1]["status"] == "off"
    assert srv["services"]["ssh"]["bad_seconds_24h"] == 0


# --- the cell the browser draws ---


def _run_js(expr):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    script = f"""
const vm = require("vm");
const fs = require("fs");
const ctx = {{
  document: {{ documentElement: {{ classList: {{ contains: () => false }} }} }},
  localStorage: {{ getItem: () => null, setItem: () => {{}} }},
  console,
}};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync({json.dumps(str(ROOT / "static/js/monitoring.js"))}, "utf8"), ctx);
process.stdout.write(JSON.stringify(vm.runInContext({json.dumps(expr)}, ctx)));
"""
    out = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_off_cell_is_grey_class_without_severity_color():
    view = _run_js('timelineCellView({status: "off", severity: 0, bad_seconds: 0})')
    assert view == {"cls": "off", "color": None}


def test_other_cells_keep_their_drawing():
    assert _run_js('timelineCellView({status: "empty", severity: 0})') == {"cls": "empty", "color": None}
    assert _run_js('timelineCellView({status: "ok", severity: 0, bad_seconds: 0})') == {"cls": "ok", "color": None}
    view = _run_js('timelineCellView({status: "fail", severity: 1, bad_seconds: 3600})')
    assert view["cls"] == "incident"
    assert view["color"] == _run_js("severityColor(1, 3600)")


def test_off_cell_css_uses_theme_variable():
    """The grey comes from --text-muted, which both themes define."""
    html = (ROOT / "templates/index.html").read_text()
    rule = next(line for line in html.splitlines() if ".uptime-block.off" in line)
    assert "background: var(--text-muted)" in rule


# --- config ---


def test_validate_rejects_non_boolean_flag():
    base = {"servers": {"L": {"host": "h", "user": "u"}}}
    base["servers"]["L"]["switched_off_ok"] = "false"
    assert config_api.validate_config(base) == ["L: switched_off_ok must be true or false"]
    base["servers"]["L"]["switched_off_ok"] = True
    assert config_api.validate_config(base) == []


def test_config_post_keeps_the_flag(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"servers": {}}))
    monkeypatch.setattr(m, "CONFIG_PATH", path)
    app = Flask(__name__)
    app.register_blueprint(config_api.config_bp)
    cfg = {"servers": {"LAPTOP": {"host": "127.0.0.1", "user": "u", "switched_off_ok": True}}}
    resp = app.test_client().post("/api/config", json=cfg)
    assert resp.status_code == 200
    assert json.loads(path.read_text())["servers"]["LAPTOP"]["switched_off_ok"] is True
