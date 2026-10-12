import config_api as c

def test_cockpit_status_running():
    assert c.parse_cockpit_status("active\n/usr/bin/cockpit-bridge") == "running"

def test_cockpit_status_installed_stopped():
    assert c.parse_cockpit_status("inactive\n/usr/bin/cockpit-bridge") == "installed-but-stopped"

def test_cockpit_status_absent():
    assert c.parse_cockpit_status("inactive\n") == "absent"

def test_install_command_apt():
    cmd = c.install_cockpit_command("apt-get")
    assert "apt-get install -y cockpit" in cmd
    assert "enable --now cockpit.socket" in cmd

def test_install_command_dnf():
    assert "dnf install -y cockpit" in c.install_cockpit_command("dnf")

def test_pkg_manager_probe_command_lists_managers():
    probe = c.pkg_manager_probe_command()
    for mgr in ("apt-get", "dnf", "zypper", "pacman"):
        assert mgr in probe


# --- /api/server/<name>/cockpit-status: answered from a cache -----------------
# Every opening of Net View asked every server over a fresh SSH session; on the
# Pi that took 7–18 s per tile (2026-10-12). Whether Cockpit is installed
# changes by hand, rarely, so a definite answer is kept for an hour.

import pytest
from flask import Flask

import monitoring

CFG = {"servers": {
    "web": {"host": "web.example", "user": "root"},
    "db": {"host": "db.example", "user": "root", "cockpit_url": "https://db.example/rhc/"},
}}


@pytest.fixture
def ssh(monkeypatch):
    """Stand-in for SSH: counts sessions, answers from `ssh.output[server]`."""
    calls = []

    class _Fake:
        output = {"web": "active\n/usr/bin/cockpit-bridge", "db": "inactive\n"}
        fail = set()

    _Fake.calls = calls

    def get_client(name, srv, cfg):
        calls.append(name)
        if name in _Fake.fail:
            raise OSError("ssh down")
        return name

    monkeypatch.setattr(monitoring, "load_config", lambda: CFG)
    monkeypatch.setattr(monitoring, "_get_ssh_client", get_client)
    monkeypatch.setattr(monitoring, "_ssh_exec", lambda client, cmd, timeout=8: _Fake.output[client])
    monkeypatch.setattr(monitoring, "_close_ssh_client", lambda client: None)
    now = [1000.0]
    monkeypatch.setattr(c, "_now", lambda: now[0])
    _Fake.now = now
    c._cockpit_cache.clear()
    return _Fake


@pytest.fixture
def client():
    app = Flask(__name__)
    app.register_blueprint(c.config_bp)
    return app.test_client()


def test_second_request_is_answered_without_ssh(ssh, client):
    first = client.get("/api/server/web/cockpit-status").get_json()
    second = client.get("/api/server/web/cockpit-status").get_json()
    assert first == second == {"status": "running", "url": "https://web.example:9090"}
    assert ssh.calls == ["web"]


def test_cache_is_per_server(ssh, client):
    assert client.get("/api/server/web/cockpit-status").get_json()["status"] == "running"
    db = client.get("/api/server/db/cockpit-status").get_json()
    assert db == {"status": "absent", "url": "https://db.example/rhc/"}
    assert ssh.calls == ["web", "db"]


def test_answer_expires_after_an_hour(ssh, client):
    client.get("/api/server/web/cockpit-status")
    ssh.now[0] += c.COCKPIT_STATUS_TTL - 1
    client.get("/api/server/web/cockpit-status")
    assert ssh.calls == ["web"]
    ssh.output = {**ssh.output, "web": "inactive\n/usr/bin/cockpit-bridge"}
    ssh.now[0] += 2
    r = client.get("/api/server/web/cockpit-status").get_json()
    assert r["status"] == "installed-but-stopped"
    assert ssh.calls == ["web", "web"]


def test_ssh_failure_is_not_cached(ssh, client):
    ssh.fail = {"web"}
    r = client.get("/api/server/web/cockpit-status").get_json()
    assert r["status"] == "unknown" and "ssh down" in r["error"]
    ssh.fail = set()
    assert client.get("/api/server/web/cockpit-status").get_json()["status"] == "running"
    assert ssh.calls == ["web", "web"]


def test_url_follows_the_live_config_not_the_cache(ssh, client, monkeypatch):
    client.get("/api/server/web/cockpit-status")
    moved = {"servers": {"web": {**CFG["servers"]["web"], "cockpit_url": "https://hitro/rhc/web/"}}}
    monkeypatch.setattr(monitoring, "load_config", lambda: moved)
    r = client.get("/api/server/web/cockpit-status").get_json()
    assert r == {"status": "running", "url": "https://hitro/rhc/web/"}
    assert ssh.calls == ["web"]


def test_install_forgets_the_cached_answer(ssh, client, monkeypatch):
    client.get("/api/server/db/cockpit-status")
    monkeypatch.setattr(c, "_run_install", lambda name, srv, cfg: None)
    assert client.post("/api/server/db/install-cockpit").get_json() == {"status": "started"}
    ssh.output = {**ssh.output, "db": "active\n/usr/bin/cockpit-bridge"}
    assert client.get("/api/server/db/cockpit-status").get_json()["status"] == "running"
    assert ssh.calls == ["db", "db"]
