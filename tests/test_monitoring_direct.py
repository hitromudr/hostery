"""Direct reachability next to liveness (owner's word 2026-11-13).

A node reached through a jump looked green while its own address was cut off
from the monitor's network — "alive" and "reachable directly" were one bit.
Now `direct_check` adds a `direct` row: the monitor itself opens host:ssh_port
and waits for the SSH banner, no jump, no proxy. `fallback_jump` makes the
liveness check try the node directly first and only then through that jump,
so a node reachable only around the block is `ssh` ok + `direct` warning.
"""
import socket
import threading

import pytest

import monitoring as m


def _banner_server(payload):
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)

    def serve():
        conn, _ = srv.accept()
        if payload:
            conn.sendall(payload)
        try:
            conn.settimeout(0.5)
            conn.recv(64)
        except OSError:
            pass
        conn.close()
        srv.close()

    threading.Thread(target=serve, daemon=True).start()
    return srv.getsockname()[1]


def test_probe_direct_reads_the_ssh_banner():
    port = _banner_server(b"SSH-2.0-OpenSSH_10.0\r\n")
    m.probe_direct({"host": "127.0.0.1", "ssh_port": port}, timeout=2)


def test_probe_direct_sends_client_banner_and_fails_on_banner_exchange_blackhole():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    received = []

    def blackhole_after_client_banner():
        conn, _ = srv.accept()
        conn.sendall(b"SSH-2.0-OpenSSH_9.2p1\r\n")
        received.append(conn.recv(64))
        # Simulate MGTS DPI filter: keep socket open without sending KEXINIT or FIN
        import time
        time.sleep(0.5)
        conn.close()
        srv.close()

    threading.Thread(target=blackhole_after_client_banner, daemon=True).start()
    with pytest.raises((TimeoutError, socket.timeout, OSError)):
        m.probe_direct({"host": "127.0.0.1", "ssh_port": port}, timeout=0.2)
    assert received and received[0].startswith(b"SSH-2.0-")


def test_probe_direct_rejects_a_port_that_is_not_ssh():
    port = _banner_server(b"HTTP/1.1 400 Bad Request\r\n")
    with pytest.raises(OSError, match="no SSH banner"):
        m.probe_direct({"host": "127.0.0.1", "ssh_port": port}, timeout=2)


def test_probe_direct_refused():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    with pytest.raises(OSError):
        m.probe_direct({"host": "127.0.0.1", "ssh_port": port}, timeout=2)


class _Client:
    def exec_command(self, *a, **k):
        raise AssertionError("no service checks in these configs")

    def close(self):
        pass


def _run(monkeypatch, cfg, direct_ok):
    seen = []

    def probe(server_cfg, timeout):
        if not direct_ok:
            raise TimeoutError("timed out")

    def connect(name, server_cfg, config):
        seen.append(server_cfg.get("jump"))
        return _Client()

    monkeypatch.setattr(m, "probe_direct", probe)
    monkeypatch.setattr(m, "_get_ssh_client", connect)
    monkeypatch.setattr(m.time, "sleep", lambda *_: None)
    results = m.check_server("EMMET", cfg, {"ssh_timeout": 1, "servers": {"GESS": {}}})
    return {r[0]: r[1] for r in results}, seen


NODE = {"host": "192.0.2.1", "user": "dms", "direct_check": True, "fallback_jump": "GESS"}


def test_direct_ok_connects_without_the_jump(monkeypatch):
    status, seen = _run(monkeypatch, dict(NODE), direct_ok=True)
    assert status == {"direct": "ok", "ssh": "ok"}
    assert seen == [None]


def test_only_around_the_block_is_alive_with_a_direct_warning(monkeypatch):
    status, seen = _run(monkeypatch, dict(NODE), direct_ok=False)
    assert status == {"direct": "warning", "ssh": "ok"}
    assert seen == ["GESS"]


def test_direct_check_alone_keeps_the_route_as_configured(monkeypatch):
    cfg = {"host": "192.0.2.1", "user": "dms", "direct_check": True, "jump": "POLKA"}
    status, seen = _run(monkeypatch, cfg, direct_ok=False)
    assert status == {"direct": "warning", "ssh": "ok"}
    assert seen == ["POLKA"]


def test_no_direct_row_unless_asked(monkeypatch):
    status, seen = _run(monkeypatch, {"host": "192.0.2.1", "user": "dms"}, direct_ok=False)
    assert status == {"ssh": "ok"}
    assert seen == [None]


def test_dead_node_keeps_the_direct_row(monkeypatch):
    def probe(server_cfg, timeout):
        raise TimeoutError("timed out")

    def connect(name, server_cfg, config):
        raise TimeoutError("timed out")

    monkeypatch.setattr(m, "probe_direct", probe)
    monkeypatch.setattr(m, "_get_ssh_client", connect)
    monkeypatch.setattr(m.time, "sleep", lambda *_: None)
    results = m.check_server("EMMET", dict(NODE), {"ssh_timeout": 1, "servers": {}})
    status = {r[0]: r[1] for r in results}
    assert status["ssh"] == "fail" and status["direct"] == "warning"


def test_switched_off_node_reports_direct_off(monkeypatch):
    cfg = dict(NODE, switched_off_ok=True)

    def probe(server_cfg, timeout):
        raise TimeoutError("timed out")

    def connect(name, server_cfg, config):
        raise TimeoutError("timed out")

    monkeypatch.setattr(m, "probe_direct", probe)
    monkeypatch.setattr(m, "_get_ssh_client", connect)
    monkeypatch.setattr(m.time, "sleep", lambda *_: None)
    results = m.check_server("LAPTOP", cfg, {"ssh_timeout": 1, "servers": {}})
    status = {r[0]: r[1] for r in results}
    assert status == {"ssh": "off", "direct": "off"}


def test_config_rejects_bad_direct_keys():
    import config_api
    base = {"servers": {"A": {"host": "h", "user": "u", "direct_check": "yes", "fallback_jump": "NOPE"},
                        "B": {"host": "h", "user": "u", "fallback_jump": "B"}}}
    assert config_api.validate_config(base) == [
        "A: direct_check must be true or false",
        "A: fallback_jump 'NOPE' is not another server",
        "B: fallback_jump 'B' is not another server",
    ]
    ok = {"servers": {"A": {"host": "h", "user": "u", "direct_check": True, "fallback_jump": "B"},
                      "B": {"host": "h", "user": "u"}}}
    assert config_api.validate_config(ok) == []


def _status_after_cycle(tmp_path, monkeypatch, server_cfg, results):
    from flask import Flask
    monkeypatch.setattr(m, "DB_PATH", tmp_path / "mon.db")
    monkeypatch.setattr(m, "load_config", lambda: {
        "check_interval": 300, "servers": {"S": dict(server_cfg, alerts=False)}})
    monkeypatch.setattr(m, "check_server", lambda name, cfg, conf: results)
    m.init_db()
    m.run_check_cycle()
    with Flask(__name__).test_request_context("/"):
        return m.monitoring_status().get_json()["servers"]["S"]


def test_status_api_reports_the_direct_row(tmp_path, monkeypatch):
    # 2026-11-13: rows were stored but the API listed only ssh + services +
    # custom checks, so `direct` never reached the dashboard.
    srv = _status_after_cycle(tmp_path, monkeypatch, NODE,
                              [("direct", "warning", "not reachable directly: timed out", 5000),
                               ("ssh", "ok", None, 12)])
    assert srv["services"]["direct"]["status"] == "warning"
    assert srv["services"]["direct"]["error"] == "not reachable directly: timed out"
    assert srv["services"]["ssh"]["status"] == "ok"


def test_status_api_has_no_direct_row_unless_asked(tmp_path, monkeypatch):
    srv = _status_after_cycle(tmp_path, monkeypatch, {"host": "x", "user": "y"},
                              [("ssh", "ok", None, 12)])
    assert set(srv["services"]) == {"ssh"}


def test_get_ssh_client_follows_jump_host_fallback_chain_without_cycles(monkeypatch):
    m.clear_jump_neg_cache()
    connected_hosts = []

    class _FakeTransport:
        def open_channel(self, kind, dest, local, timeout=None):
            return ("chan", dest)

        def set_keepalive(self, interval):
            pass

    class _FakeSSHClient:
        def set_missing_host_key_policy(self, policy):
            pass

        def connect(self, hostname=None, username=None, key_filename=None, timeout=None, port=22, sock=None):
            connected_hosts.append((hostname, sock))
            if hostname == "193.148.59.31":
                raise TimeoutError("STOCK timed out")

        def get_transport(self):
            return _FakeTransport()

        def close(self):
            pass

    monkeypatch.setattr(m.paramiko, "SSHClient", _FakeSSHClient)
    config = {
        "ssh_timeout": 1,
        "servers": {
            "POLKA": {"host": "213.5.128.224", "user": "hitromudr", "jump": "STOCK"},
            "STOCK": {"host": "193.148.59.31", "user": "dms", "fallback_jump": "GESS"},
            "GESS": {"host": "5.231.56.198", "user": "dms", "fallback_jump": "STOCK"},
        },
    }
    client = m._get_ssh_client("POLKA", config["servers"]["POLKA"], config)
    assert client is not None
    assert connected_hosts == [
        ("193.148.59.31", None),
        ("5.231.56.198", None),
        ("213.5.128.224", ("chan", ("213.5.128.224", 22))),
    ]


def test_get_ssh_client_falls_back_when_first_jump_open_channel_times_out(monkeypatch):
    m.clear_jump_neg_cache()
    connected_hosts = []
    closed_hosts = []

    class _FakeTransport:
        def __init__(self, host):
            self.host = host

        def open_channel(self, kind, dest, local, timeout=None):
            if self.host == "5.231.56.198":
                raise m.paramiko.SSHException("Timeout opening channel.")
            return ("chan", dest)

        def set_keepalive(self, interval):
            pass

    class _FakeSSHClient:
        def __init__(self):
            self.host = None

        def set_missing_host_key_policy(self, policy):
            pass

        def connect(self, hostname=None, username=None, key_filename=None, timeout=None, port=22, sock=None):
            self.host = hostname
            connected_hosts.append((hostname, sock))

        def get_transport(self):
            return _FakeTransport(self.host)

        def close(self):
            closed_hosts.append(self.host)

    monkeypatch.setattr(m.paramiko, "SSHClient", _FakeSSHClient)
    config = {
        "ssh_timeout": 1,
        "servers": {
            "FINKA": {"host": "5.181.21.235", "user": "dms", "jump": "GESS"},
            "GESS": {"host": "5.231.56.198", "user": "dms", "fallback_jump": "TALLIN"},
            "TALLIN": {"host": "141.98.166.243", "user": "dms", "fallback_jump": "AEZA"},
        },
    }
    client = m._get_ssh_client("FINKA", config["servers"]["FINKA"], config)
    assert client is not None
    assert connected_hosts == [
        ("5.231.56.198", None),
        ("141.98.166.243", None),
        ("5.181.21.235", ("chan", ("5.181.21.235", 22))),
    ]
    assert "5.231.56.198" in closed_hosts


def test_get_ssh_client_negative_caches_failed_jump_hosts_and_continues_via_default_jumps(monkeypatch):
    """Инцидент 21.11.2026 06:17 UTC: TALLIN не ответил, AEZA сбросила сессию с
    SSHException('No existing session') при fallback_jump=null в config.json, и
    все 8 узлов упали на AEZA, не попробовав JERSEY / TBILISI / EMMET и тратя
    по 10 с таймаута на мёртвых прыжках в каждом воркере."""
    m.clear_jump_neg_cache()
    connected_hosts = []
    now = [1000.0]
    monkeypatch.setattr(m.time, "monotonic", lambda: now[0])

    class _FakeTransport:
        def open_channel(self, kind, dest, local, timeout=None):
            return ("chan", dest)

        def set_keepalive(self, interval):
            pass

    class _FakeSSHClient:
        def set_missing_host_key_policy(self, policy):
            pass

        def connect(self, hostname=None, username=None, key_filename=None, timeout=None, port=22, sock=None):
            connected_hosts.append((hostname, sock))
            if hostname == "141.98.166.243":
                raise TimeoutError("TALLIN timed out")
            if hostname == "89.22.236.42":
                raise m.paramiko.SSHException("No existing session")

        def get_transport(self):
            return _FakeTransport()

        def close(self):
            pass

    monkeypatch.setattr(m.paramiko, "SSHClient", _FakeSSHClient)
    assert "JERSEY" not in m.DEFAULT_FALLBACK_JUMPS
    config = {
        "ssh_timeout": 1,
        "jump_neg_ttl": 180,
        "servers": {
            "POLKA": {"host": "213.5.128.224", "user": "hitromudr", "jump": "TALLIN"},
            "STOCK": {"host": "193.148.59.31", "user": "dms", "jump": "TALLIN"},
            "TALLIN": {"host": "141.98.166.243", "user": "dms", "fallback_jump": "AEZA"},
            "AEZA": {"host": "89.22.236.42", "user": "dms", "fallback_jump": None},
            "JERSEY": {"host": "85.209.134.77", "user": "dms", "fallback_jump": None},
            "TBILISI": {"host": "185.163.203.76", "user": "dms", "fallback_jump": None},
        },
    }
    # First server: tries TALLIN (fails), tries AEZA (fails with No existing session),
    # automatically continues to TBILISI from DEFAULT_FALLBACK_JUMPS even though AEZA.fallback_jump is None.
    client1 = m._get_ssh_client("POLKA", config["servers"]["POLKA"], config)
    assert client1 is not None
    assert connected_hosts == [
        ("141.98.166.243", None),
        ("89.22.236.42", None),
        ("185.163.203.76", None),
        ("213.5.128.224", ("chan", ("213.5.128.224", 22))),
    ]

    # Second server within 180s TTL: skips TALLIN and AEZA immediately via negative cache
    # and connects directly through TBILISI.
    connected_hosts.clear()
    now[0] = 1050.0
    client2 = m._get_ssh_client("STOCK", config["servers"]["STOCK"], config)
    assert client2 is not None
    assert connected_hosts == [
        ("185.163.203.76", None),
        ("193.148.59.31", ("chan", ("193.148.59.31", 22))),
    ]


def test_check_server_falls_back_to_jump_when_direct_probe_ok_but_ssh_connect_fails(monkeypatch):
    """Если probe_direct прошёл (баннер получен), а следом прямое Paramiko-соединение
    было сброшено с 'No existing session', check_server обязан перейти на fallback_jump."""
    seen_jumps = []

    def connect(name, server_cfg, config):
        seen_jumps.append(server_cfg.get("jump"))
        if server_cfg.get("jump") is None:
            raise m.paramiko.SSHException("No existing session")
        return _Client()

    monkeypatch.setattr(m, "probe_direct", lambda server_cfg, timeout: None)
    monkeypatch.setattr(m, "_get_ssh_client", connect)
    monkeypatch.setattr(m.time, "sleep", lambda *_: None)
    results = m.check_server("AEZA", dict(NODE, fallback_jump="TBILISI"), {"ssh_timeout": 1, "servers": {"TBILISI": {}}})
    status = {r[0]: r[1] for r in results}
    assert status == {"direct": "warning", "ssh": "ok"}
    assert seen_jumps == [None, "TBILISI"]


def test_get_ssh_client_marks_direct_socket_with_direct_probe_tos(monkeypatch):
    """Инцидент 21.11.2026 14:53 UTC: при режиме pin-mac на MAC, когда probe_direct
    с IP_TOS=0x04 проходил, прямое соединение _get_ssh_client без IP_TOS=0x04
    перехватывалось правилом hitro-ssh-detour-redirect (! --tos 0x04 -> :2231)
    и попадало на sshd-bastion MAC вместо реального HITRO."""
    port = _banner_server(b"SSH-2.0-OpenSSH_10.0\r\n")
    captured_sock = []

    class _FakeTransport:
        def set_keepalive(self, interval):
            pass

    class _FakeSSHClient:
        def set_missing_host_key_policy(self, policy):
            pass

        def connect(self, **kwargs):
            captured_sock.append(kwargs.get("sock"))

        def get_transport(self):
            return _FakeTransport()

        def close(self):
            pass

    monkeypatch.setattr(m.paramiko, "SSHClient", _FakeSSHClient)
    cfg = {"host": "127.0.0.1", "ssh_port": port, "user": "root", "direct_check": True, "fallback_jump": "TALLIN"}
    m._get_ssh_client("HITRO", cfg, {"ssh_timeout": 2})
    assert len(captured_sock) == 1 and captured_sock[0] is not None
    try:
        tos = captured_sock[0].getsockopt(socket.IPPROTO_IP, socket.IP_TOS)
        assert (tos & m.DIRECT_PROBE_TOS) == m.DIRECT_PROBE_TOS
    finally:
        captured_sock[0].close()



