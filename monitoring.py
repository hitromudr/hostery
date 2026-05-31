import logging

logger = logging.getLogger(__name__)


def resolve_service(entry):
    """Normalize a service config entry to (name, check_dict).

    A bare string is shorthand for a systemd unit of the same name.
    A dict must carry 'name' and 'type' plus type-specific fields.
    """
    if isinstance(entry, str):
        return entry, {"type": "systemctl", "unit": entry}
    name = entry["name"]
    check = {k: v for k, v in entry.items() if k != "name"}
    check.setdefault("type", "systemctl")
    if check["type"] == "systemctl":
        check.setdefault("unit", name)
    return name, check


def service_names(services):
    return [resolve_service(s)[0] for s in services]


def _eval_expect(expr, val, raw):
    expr = (expr or "").strip()
    for op in ("<=", ">=", "==", "!=", "<", ">"):
        if expr.startswith(op):
            try:
                threshold = int(expr[len(op):].strip())
            except (ValueError, TypeError):
                return bool(raw)
            return {
                "<=": val <= threshold, ">=": val >= threshold,
                "==": val == threshold, "!=": val != threshold,
                "<": val < threshold, ">": val > threshold,
            }[op]
    return bool(raw)


def build_check_command(check):
    """Return the remote shell command for a non-systemctl check type, or None."""
    t = check["type"]
    if t == "port":
        port = check["port"]
        return f"ss -tlnp 2>/dev/null | grep -q ':{port} ' && echo listening || echo closed"
    if t == "docker":
        c = check["container"]
        return f"sudo docker ps --filter name={c} --format '{{{{.Status}}}}' 2>/dev/null | head -1"
    if t in ("interface", "wireguard"):
        iface = check.get("iface", "wg0" if t == "wireguard" else "")
        return f"ip link show {iface} 2>/dev/null | head -1"
    if t == "http":
        url = check["url"]
        return f"curl -so /dev/null -w '%{{http_code}}' --max-time 5 {url} 2>/dev/null || echo 000"
    return None
