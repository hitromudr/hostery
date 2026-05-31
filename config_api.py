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
