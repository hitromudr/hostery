import copy
import logging

logger = logging.getLogger(__name__)

VALID_CHECK_TYPES = {"systemctl", "port", "docker", "interface", "wireguard", "http", "command"}


def validate_config(cfg):
    """Return a list of human-readable error strings; empty means valid."""
    errors = []
    if not isinstance(cfg, dict):
        return ["config must be an object"]
    servers = cfg.get("servers")
    if not isinstance(servers, dict):
        return ["config.servers must be an object"]
    for name, srv in servers.items():
        if not srv.get("host"):
            errors.append(f"{name}: missing host")
        if not srv.get("user"):
            errors.append(f"{name}: missing user")
        for entry in srv.get("services", []):
            if isinstance(entry, str):
                continue
            if not isinstance(entry, dict) or "name" not in entry:
                errors.append(f"{name}: service entry must be a string or an object with 'name'")
                continue
            t = entry.get("type", "systemctl")
            if t not in VALID_CHECK_TYPES:
                errors.append(f"{name}/{entry.get('name')}: unknown check type '{t}'")
    return errors


def redact(cfg):
    """Return a deep copy safe to send to the browser (telegram token blanked)."""
    red = copy.deepcopy(cfg)
    if isinstance(red.get("telegram"), dict):
        red["telegram"]["bot_token"] = ""
    return red


def parse_cockpit_status(output):
    """Map combined output of `systemctl is-active cockpit.socket` + `command -v cockpit-bridge`."""
    lines = [l.strip() for l in (output or "").splitlines()]
    active = lines[0] if lines else ""
    has_bridge = len(lines) > 1 and bool(lines[1])
    if active == "active":
        return "running"
    if has_bridge:
        return "installed-but-stopped"
    return "absent"


def cockpit_status_command():
    return "systemctl is-active cockpit.socket 2>/dev/null; command -v cockpit-bridge 2>/dev/null"


def pkg_manager_probe_command():
    return ("for m in apt-get dnf zypper pacman; do "
            "command -v $m >/dev/null 2>&1 && echo $m && break; done")


def install_cockpit_command(pkg_manager):
    installers = {
        "apt-get": "sudo apt-get update && sudo apt-get install -y cockpit",
        "dnf": "sudo dnf install -y cockpit",
        "zypper": "sudo zypper --non-interactive install cockpit",
        "pacman": "sudo pacman -S --noconfirm cockpit",
    }
    install = installers.get(pkg_manager)
    if not install:
        return None
    return f"{install} && sudo systemctl enable --now cockpit.socket && echo HOSTERY_INSTALL_OK"


import json
import threading

from flask import Blueprint, Response, jsonify, request

import monitoring

config_bp = Blueprint("config", __name__)

_install_logs = {}  # server_name -> list[str], streamed install output


@config_bp.route("/api/config")
def get_config():
    return jsonify(redact(monitoring.load_config()))


@config_bp.route("/api/config/path")
def get_config_path():
    return jsonify({"path": str(monitoring.CONFIG_PATH)})


@config_bp.route("/api/config/raw")
def get_config_raw():
    # Raw view of the config as it is on disk, pretty-printed. The telegram
    # token is redacted here too — never hand the secret to the browser.
    cfg = redact(monitoring.load_config())
    body = json.dumps(cfg, indent=2, ensure_ascii=False)
    return Response(body, mimetype="text/plain; charset=utf-8")


@config_bp.route("/api/config", methods=["POST"])
def post_config():
    cfg = request.json or {}
    # Preserve the real telegram token if the client sent a blanked one.
    current = monitoring.load_config()
    if cfg.get("telegram", {}).get("bot_token", "") == "" and current.get("telegram", {}).get("bot_token"):
        cfg.setdefault("telegram", {})["bot_token"] = current["telegram"]["bot_token"]
    errors = validate_config(cfg)
    if errors:
        return jsonify({"errors": errors}), 400
    tmp = str(monitoring.CONFIG_PATH) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2)
    import os
    os.replace(tmp, str(monitoring.CONFIG_PATH))
    return jsonify({"status": "saved"})


@config_bp.route("/api/config/test-ssh", methods=["POST"])
def test_ssh():
    data = request.json or {}
    name = data.get("server")
    cfg = monitoring.load_config()
    srv = cfg.get("servers", {}).get(name)
    if not srv:
        return jsonify({"error": f"server {name} not found"}), 404
    try:
        client = monitoring._get_ssh_client(name, srv, cfg)
        out = monitoring._ssh_exec(client, "echo ok")
        monitoring._close_ssh_client(client)
        return jsonify({"status": "ok" if out == "ok" else "weird", "output": out})
    except Exception as e:
        return jsonify({"status": "fail", "error": str(e)}), 200


@config_bp.route("/api/server/<name>/cockpit-status")
def cockpit_status(name):
    cfg = monitoring.load_config()
    srv = cfg.get("servers", {}).get(name)
    if not srv:
        return jsonify({"error": "not found"}), 404
    try:
        client = monitoring._get_ssh_client(name, srv, cfg)
        out = monitoring._ssh_exec(client, cockpit_status_command())
        monitoring._close_ssh_client(client)
        status = parse_cockpit_status(out)
        url = srv.get("cockpit_url") or f"https://{srv['host']}:9090"
        return jsonify({"status": status, "url": url})
    except Exception as e:
        return jsonify({"status": "unknown", "error": str(e)}), 200


def _run_install(name, srv, cfg):
    log = _install_logs[name] = []
    try:
        client = monitoring._get_ssh_client(name, srv, cfg)
        mgr = monitoring._ssh_exec(client, pkg_manager_probe_command())
        log.append(f"package manager: {mgr or 'none found'}")
        cmd = install_cockpit_command(mgr)
        if not cmd:
            log.append("ERROR: no supported package manager (apt-get/dnf/zypper/pacman)")
            monitoring._close_ssh_client(client)
            return
        out = monitoring._ssh_exec(client, cmd, timeout=120)
        log.append(out)
        log.append("DONE" if "HOSTERY_INSTALL_OK" in out else
                   "ERROR: install did not confirm (need root/passwordless sudo? port 9090 firewall?)")
        monitoring._close_ssh_client(client)
    except Exception as e:
        log.append(f"ERROR: {e}")


@config_bp.route("/api/server/<name>/install-cockpit", methods=["POST"])
def install_cockpit(name):
    cfg = monitoring.load_config()
    srv = cfg.get("servers", {}).get(name)
    if not srv:
        return jsonify({"error": "not found"}), 404
    threading.Thread(target=_run_install, args=(name, srv, cfg), daemon=True).start()
    return jsonify({"status": "started"})


@config_bp.route("/api/server/<name>/install-cockpit/log")
def install_cockpit_log(name):
    return jsonify({"log": _install_logs.get(name, [])})
