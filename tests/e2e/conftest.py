"""E2E harness: launch hostery as a real subprocess against a deterministic
test config, then drive the SPA with Playwright over basic auth.

The fake fleet points at 127.0.0.1 so every SSH-backed action (background
check cycle, test-connection, cockpit-status) fails FAST (connection refused
or auth failure) instead of hanging on an unreachable host. The UI is asserted
to render and degrade gracefully regardless of SSH outcome.
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO / "config"
CONFIG_PATH = CONFIG_DIR / "config.json"
DB_PATH = CONFIG_DIR / "monitoring.db"

USER, PW = "admin", "pw"

TEST_CONFIG = {
    "check_interval": 3600,           # run the check cycle once at startup, not repeatedly
    "ssh_timeout": 3,                 # fail fast against the fake fleet
    "retention_days": 90,
    "telegram": {"bot_token": "", "chat_id": "55555"},
    "servers": {
        "web-01": {
            "host": "127.0.0.1", "user": "root", "key": "~/.ssh/id_rsa", "cockpit_url": "",
            "services": [
                "nginx",
                {"name": "ssh-port", "type": "port", "port": 22},
                {"name": "app", "type": "docker", "container": "demo"},
                {"name": "health", "type": "http", "url": "http://127.0.0.1:9/h", "expect_code": 200},
            ],
            "custom_checks": [
                {"name": "disk-free", "command": "echo 50", "expect": "> 10",
                 "severity": "warning", "description": "Root fs free percent"},
            ],
        },
        "vpn-01": {
            "host": "127.0.0.1", "user": "admin",
            "services": [{"name": "wg", "type": "wireguard", "iface": "wg0"}],
        },
    },
}


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _clean_db():
    for ext in ("", "-wal", "-shm"):
        f = Path(str(DB_PATH) + ext)
        if f.exists():
            f.unlink()


def _write_test_config():
    CONFIG_PATH.write_text(json.dumps(TEST_CONFIG, indent=2))


@pytest.fixture(scope="session")
def hostery_server():
    # Back up whatever local config / db exists so the dev's instance is untouched.
    backups = {}
    for p in (CONFIG_PATH, DB_PATH):
        if p.exists():
            b = Path(str(p) + ".e2ebak")
            shutil.copy2(p, b)
            backups[p] = b
    _clean_db()
    _write_test_config()

    port = _free_port()
    env = {**os.environ, "HOSTERY_AUTH": f"{USER}:{PW}",
           "HOSTERY_BIND": "127.0.0.1", "HOSTERY_PORT": str(port)}
    log = tempfile.NamedTemporaryFile(prefix="hostery-e2e-", suffix=".log", delete=False)
    proc = subprocess.Popen([sys.executable, "app.py"], cwd=str(REPO), env=env,
                            stdout=log, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"

    ready = False
    for _ in range(60):
        if proc.poll() is not None:
            log.flush()
            raise RuntimeError("hostery exited early:\n" + Path(log.name).read_text())
        try:
            urllib.request.urlopen(base + "/api/stats", timeout=2)
        except urllib.error.HTTPError as e:
            if e.code == 401:           # auth challenge == server is up
                ready = True
                break
        except Exception:
            pass
        time.sleep(0.5)
    if not ready:
        proc.terminate()
        raise RuntimeError("hostery did not become ready:\n" + Path(log.name).read_text())

    yield {"base_url": base, "username": USER, "password": PW}

    proc.terminate()
    try:
        proc.wait(timeout=10)
    except Exception:
        proc.kill()
    log.close()
    Path(log.name).unlink(missing_ok=True)

    _clean_db()
    for orig, b in backups.items():
        shutil.move(str(b), str(orig))
    if CONFIG_PATH not in backups and CONFIG_PATH.exists():
        CONFIG_PATH.unlink()


@pytest.fixture(autouse=True)
def reset_config(hostery_server):
    """Restore the canonical test config before every test so UI mutations
    (Save / Add server) from one test never bleed into the next. The app reads
    config live per request, so no restart is needed."""
    _write_test_config()
    yield


@pytest.fixture
def browser_context_args(browser_context_args, hostery_server):
    return {
        **browser_context_args,
        "base_url": hostery_server["base_url"],
        "http_credentials": {"username": USER, "password": PW},
        "ignore_https_errors": True,
    }
