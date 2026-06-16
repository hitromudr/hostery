import json
import logging
import os
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

import paramiko
import requests
from flask import Blueprint, jsonify, request

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


monitoring_bp = Blueprint("monitoring", __name__)

# Config dir defaults to ./config but can be redirected (e.g. for demo/test
# runs) via HOSTERY_CONFIG_DIR so real config.json / monitoring.db are untouched.
CONFIG_DIR = Path(os.environ.get("HOSTERY_CONFIG_DIR", Path(__file__).parent / "config"))
DB_PATH = CONFIG_DIR / "monitoring.db"
CONFIG_PATH = CONFIG_DIR / "config.json"

_lock = threading.Lock()
_scheduler_started = False
_last_check_time = None
_next_check_time = None


# --- Database ---


def get_db():
    conn = sqlite3.connect(str(DB_PATH), timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    conn = get_db()
    # Migrate: add columns if they don't exist
    try:
        conn.execute("ALTER TABLE incidents ADD COLUMN error_msg TEXT")
    except Exception:
        pass
    try:
        conn.execute("ALTER TABLE incidents ADD COLUMN suppressed_until TEXT")
    except Exception:
        pass
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS checks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            server TEXT NOT NULL,
            service TEXT NOT NULL,
            status TEXT NOT NULL,
            response_time_ms INTEGER,
            error_msg TEXT,
            UNIQUE(timestamp, server, service)
        );
        CREATE INDEX IF NOT EXISTS idx_checks_ts ON checks(timestamp);
        CREATE INDEX IF NOT EXISTS idx_checks_server ON checks(server, timestamp);

        CREATE TABLE IF NOT EXISTS incidents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            server TEXT NOT NULL,
            service TEXT NOT NULL,
            started_at TEXT NOT NULL,
            resolved_at TEXT,
            error_msg TEXT,
            notified INTEGER DEFAULT 0,
            suppressed_until TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_incidents_open
            ON incidents(resolved_at) WHERE resolved_at IS NULL;
    """)
    conn.close()


def prune_old_checks(days):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    conn = get_db()
    conn.execute("DELETE FROM checks WHERE timestamp < ?", (cutoff,))
    conn.commit()
    conn.close()


# --- Config ---


def load_config():
    with open(CONFIG_PATH, "r") as f:
        return json.load(f)


# --- SSH Checks ---


def _get_ssh_client(server_name, server_cfg, config):
    """Create paramiko SSHClient for a server, handling jump hosts."""
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    timeout = config.get("ssh_timeout", 10)
    key_path = server_cfg.get("key")
    if key_path:
        key_path = os.path.expanduser(key_path)

    jump_name = server_cfg.get("jump")
    sock = None

    if jump_name:
        # Connect through jump host
        jump_cfg = config["servers"][jump_name]
        jump_key = jump_cfg.get("key")
        if jump_key:
            jump_key = os.path.expanduser(jump_key)

        jump_client = paramiko.SSHClient()
        jump_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        jump_client.connect(
            hostname=jump_cfg["host"],
            username=jump_cfg["user"],
            key_filename=jump_key,
            timeout=timeout,
        )
        transport = jump_client.get_transport()
        dest = (server_cfg["host"], 22)
        local = ("127.0.0.1", 0)
        sock = transport.open_channel("direct-tcpip", dest, local)
        # Store jump_client on the main client so we can close it later
        client._jump_client = jump_client

    # Optional SOCKS5 proxy (e.g. an autossh/ssh -D tunnel). Accepts
    # "host:port" or {"host":..., "port":...}. Mutually exclusive with jump.
    socks_cfg = server_cfg.get("socks")
    if socks_cfg and sock is None:
        import socks as _socks  # PySocks
        if isinstance(socks_cfg, str):
            sh, _, sp = socks_cfg.partition(":")
            socks_host, socks_port = sh or "127.0.0.1", int(sp or 1080)
        else:
            socks_host = socks_cfg.get("host", "127.0.0.1")
            socks_port = int(socks_cfg.get("port", 1080))
        s = _socks.socksocket()
        s.set_proxy(_socks.SOCKS5, socks_host, socks_port)
        s.settimeout(timeout)
        s.connect((server_cfg["host"], int(server_cfg.get("ssh_port", 22))))
        sock = s

    connect_kwargs = {
        "hostname": server_cfg["host"],
        "username": server_cfg["user"],
        "timeout": timeout,
    }
    if key_path:
        connect_kwargs["key_filename"] = key_path
    if sock:
        connect_kwargs["sock"] = sock

    client.connect(**connect_kwargs)
    # Enable keepalive to prevent stale connections
    transport = client.get_transport()
    if transport:
        transport.set_keepalive(30)
    return client


def _close_ssh_client(client):
    try:
        client.close()
    except Exception:
        pass
    jump = getattr(client, "_jump_client", None)
    if jump:
        try:
            jump.close()
        except Exception:
            pass


def _ssh_exec(client, cmd, timeout=8):
    """Execute SSH command with retry on channel timeout."""
    try:
        _, stdout, stderr = client.exec_command(cmd, timeout=timeout)
        return stdout.read().decode().strip()
    except Exception as e:
        if "Timeout opening channel" in str(e) or "Channel" in str(e):
            # Retry once after short delay
            time.sleep(1)
            _, stdout, stderr = client.exec_command(cmd, timeout=timeout)
            return stdout.read().decode().strip()
        raise


def check_server(server_name, server_cfg, config):
    """Run all checks for a single server. Returns list of (service, status, error)."""
    results = []
    start = time.time()

    try:
        client = _get_ssh_client(server_name, server_cfg, config)
    except Exception as e:
        elapsed = int((time.time() - start) * 1000)
        logger.warning(f"SSH connect failed for {server_name}: {e}")
        # SSH itself is down — services are unknown (can't check)
        results.append(("ssh", "fail", str(e), elapsed))
        # Resolve each entry to its service NAME — entries may be dicts in the
        # config-driven model, and a dict can't be stored as a service key.
        for entry in server_cfg.get("services", []):
            svc, _ = resolve_service(entry)
            results.append((svc, "unknown", None, 0))
        return results

    elapsed_ssh = int((time.time() - start) * 1000)
    results.append(("ssh", "ok", None, elapsed_ssh))

    services = server_cfg.get("services", [])
    for entry in services:
        svc, check = resolve_service(entry)
        svc_start = time.time()
        try:
            ctype = check["type"]
            if ctype == "systemctl":
                output = _ssh_exec(client, f"systemctl is-active {check['unit']} 2>/dev/null")
                svc_elapsed = int((time.time() - svc_start) * 1000)
                if output == "active":
                    status, error = "ok", None
                elif output in ("inactive", "dead"):
                    status, error = "inactive", None
                elif output in ("activating", "deactivating"):
                    status, error = "warning", f"Service is {output}"
                else:
                    status, error = "fail", f"Service: {output or 'unknown'}"
            elif ctype == "port":
                output = _ssh_exec(client, build_check_command(check))
                svc_elapsed = int((time.time() - svc_start) * 1000)
                status, error = ("ok", None) if output == "listening" else ("fail", f"Port {check['port']} not listening")
            elif ctype == "docker":
                output = _ssh_exec(client, build_check_command(check))
                svc_elapsed = int((time.time() - svc_start) * 1000)
                status, error = ("ok", None) if output.startswith("Up") else ("fail", f"Container: {output or 'not found'}")
            elif ctype in ("interface", "wireguard"):
                output = _ssh_exec(client, build_check_command(check))
                svc_elapsed = int((time.time() - svc_start) * 1000)
                iface = check.get("iface", "wg0" if ctype == "wireguard" else "")
                if "UP" in output:
                    status, error = "ok", None
                elif output:
                    status, error = "inactive", None
                else:
                    status, error = "fail", f"{iface} not found"
            elif ctype == "http":
                output = _ssh_exec(client, build_check_command(check))
                svc_elapsed = int((time.time() - svc_start) * 1000)
                expect_code = str(check.get("expect_code", 200))
                status, error = ("ok", None) if output == expect_code else ("fail", f"HTTP {output} (expected {expect_code})")
            else:
                svc_elapsed = int((time.time() - svc_start) * 1000)
                status, error = "fail", f"Unknown check type: {ctype}"
            results.append((svc, status, error, svc_elapsed))
        except Exception as e:
            svc_elapsed = int((time.time() - svc_start) * 1000)
            results.append((svc, "fail", str(e), svc_elapsed))

    # Custom health checks
    custom_checks = server_cfg.get("custom_checks", [])
    for check in custom_checks:
        name = check.get("name", "custom")
        command = check.get("command", "echo 0")
        expect = check.get("expect", "> 0")
        warn_if = check.get("warn_if")
        description = check.get("description", "")
        svc_start = time.time()
        try:
            output = _ssh_exec(client, command, timeout=10)
            svc_elapsed = int((time.time() - svc_start) * 1000)

            try:
                val = int(output) if output.lstrip("-").isdigit() else 0
                output_is_int = output.lstrip("-").isdigit()
            except (ValueError, TypeError, AttributeError):
                val = 0
                output_is_int = False

            passed = _eval_expect(expect, val, output) if output_is_int else bool(output)

            if passed:
                status, error = "ok", None
            else:
                severity = check.get("severity", "warning")
                # warn_if downgrades a fail to a warning when the value is
                # within tolerable bounds (e.g. small packet loss).
                if warn_if and output_is_int and _eval_expect(warn_if, val, output):
                    status = "warning"
                else:
                    status = "fail" if severity == "critical" else "warning"
                error = f"{description}: got '{output}' (expected {expect})"

            results.append((name, status, error, svc_elapsed))
        except Exception as e:
            svc_elapsed = int((time.time() - svc_start) * 1000)
            severity = check.get("severity", "warning")
            fail_status = "fail" if severity == "critical" else "warning"
            results.append((name, fail_status, f"{description}: {e}", svc_elapsed))

    _close_ssh_client(client)
    return results


# --- Incident Management ---


def update_incidents(conn, server, service, status, timestamp, error=None):
    """Track incidents: open on fail, close on recovery. Inactive is not an incident."""
    # Check for open incident
    row = conn.execute(
        "SELECT id, suppressed_until FROM incidents WHERE server=? AND service=? AND resolved_at IS NULL",
        (server, service),
    ).fetchone()

    # Only "fail" opens incidents; "inactive", "warning", "unknown" do not
    if status in ("unknown",):
        return None  # Can't determine status, skip
    if status == "fail" and not row:
        # Check if there's a suppression (resolved incident with suppressed_until in future)
        suppressed = conn.execute(
            "SELECT suppressed_until FROM incidents WHERE server=? AND service=? "
            "AND suppressed_until IS NOT NULL AND suppressed_until > ? "
            "ORDER BY id DESC LIMIT 1",
            (server, service, timestamp),
        ).fetchone()
        if suppressed:
            return None  # Suppressed, don't reopen

        conn.execute(
            "INSERT INTO incidents (server, service, started_at, error_msg) VALUES (?, ?, ?, ?)",
            (server, service, timestamp, error),
        )
        return "opened"
    elif status == "fail" and row:
        # Update error message on existing incident
        conn.execute(
            "UPDATE incidents SET error_msg=? WHERE id=?",
            (error, row["id"]),
        )
        return None
    elif status in ("ok", "inactive", "warning") and row:
        # Resolve incident (service recovered or went inactive)
        conn.execute(
            "UPDATE incidents SET resolved_at=? WHERE id=?",
            (timestamp, row["id"]),
        )
        return "resolved"
    return None


# --- Telegram ---


def send_telegram(config, message):
    """Send a Telegram notification."""
    token = config.get("telegram", {}).get("bot_token", "")
    chat_id = config.get("telegram", {}).get("chat_id", "")
    if not token or not chat_id:
        return

    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        requests.post(url, json={
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML",
        }, timeout=5)
    except Exception as e:
        logger.error(f"Telegram send failed: {e}")


def format_downtime(started_at, resolved_at):
    """Format duration between two ISO timestamps."""
    start = datetime.fromisoformat(started_at)
    end = datetime.fromisoformat(resolved_at)
    delta = end - start
    total_seconds = int(delta.total_seconds())
    if total_seconds < 60:
        return f"{total_seconds}s"
    minutes = total_seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    remaining = minutes % 60
    return f"{hours}h {remaining}m"


# --- Check Cycle ---


def run_check_cycle():
    """Execute one full check cycle across all servers."""
    global _last_check_time, _next_check_time

    config = load_config()
    timestamp = datetime.now(timezone.utc).isoformat()
    _last_check_time = timestamp

    # Check all servers in parallel
    all_results = {}
    servers = config.get("servers", {})
    if servers:
        with ThreadPoolExecutor(max_workers=len(servers)) as pool:
            futures = {
                pool.submit(check_server, name, cfg, config): name
                for name, cfg in servers.items()
            }
            for future in as_completed(futures):
                name = futures[future]
                try:
                    all_results[name] = future.result()
                except Exception as e:
                    logger.error(f"Check thread error for {name}: {e}")
                    all_results[name] = [("ssh", "fail", str(e), 0)]

    conn = get_db()

    for server_name, results in all_results.items():
        for service, status, error, response_ms in results:
            # Store check result
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO checks (timestamp, server, service, status, response_time_ms, error_msg) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (timestamp, server_name, service, status, response_ms, error),
                )
            except Exception as e:
                logger.error(f"DB insert error: {e}")

            # Update incidents and send alerts
            change = update_incidents(conn, server_name, service, status, timestamp, error)
            if change == "opened":
                msg = (
                    f"\U0001f534 <b>{server_name}</b>: {service} <b>DOWN</b>\n"
                    f"Error: {error or 'unknown'}\n"
                    f"Time: {timestamp[:19]}"
                )
                send_telegram(config, msg)
            elif change == "resolved":
                # Get incident for downtime calculation
                incident = conn.execute(
                    "SELECT started_at FROM incidents WHERE server=? AND service=? AND resolved_at=? ORDER BY id DESC LIMIT 1",
                    (server_name, service, timestamp),
                ).fetchone()
                downtime = ""
                if incident:
                    downtime = f"\nDowntime: {format_downtime(incident['started_at'], timestamp)}"
                msg = (
                    f"\U0001f7e2 <b>{server_name}</b>: {service} <b>recovered</b>"
                    f"{downtime}"
                )
                send_telegram(config, msg)

    conn.commit()

    # Prune old data
    retention = config.get("retention_days", 90)
    prune_old_checks(retention)

    conn.close()

    # Set next check time after the current cycle completes so the frontend
    # countdown reflects when the *next* cycle will actually start.
    interval = config.get("check_interval", 300)
    _next_check_time = (
        datetime.now(timezone.utc) + timedelta(seconds=interval)
    ).isoformat()

    logger.info(f"Check cycle completed at {timestamp}")


# --- Background Scheduler ---


def _scheduler_loop():
    """Runs check cycles in a background thread."""
    while True:
        try:
            run_check_cycle()
        except Exception as e:
            logger.error(f"Check cycle error: {e}")

        config = load_config()
        interval = config.get("check_interval", 300)
        time.sleep(interval)


def start_scheduler():
    """Start the background monitoring thread."""
    global _scheduler_started
    with _lock:
        if _scheduler_started:
            return
        _scheduler_started = True

    t = threading.Thread(target=_scheduler_loop, daemon=True, name="monitor-scheduler")
    t.start()
    logger.info("Monitoring scheduler started")


# --- Timeline computation ---
#
# Single source of truth for "how bad was service X over the last N hours".
# Both the per-server uptime bar and the per-service dot/strip feed off the
# same intervals → same hourly buckets → same severity numbers → same color
# scale on the frontend. No second metric, no second formula.


def _severity_curve(bad_seconds_per_hour):
    """Map bad-seconds-in-an-hour (0..3600) to severity (0..1) with a non-
    linear ramp: brief blips stay near green, sustained downtime escalates."""
    x = min(1.0, bad_seconds_per_hour / 3600.0)
    return (x * 2.0) ** 2 / 2.0 if x <= 0.5 else x


def _build_intervals(svc_rows, now_utc):
    """Convert one service's check rows (ASC) into a list of bad-run intervals.

    Each interval covers a contiguous span where status was in {fail, warning}.
    Open runs (no subsequent good check in the window) end at now_utc and
    carry resolved_at=None so the frontend can show "still ongoing".
    """
    intervals = []
    i = 0
    while i < len(svc_rows):
        if svc_rows[i]["status"] not in ("fail", "warning"):
            i += 1
            continue
        run_start_iso = svc_rows[i]["timestamp"]
        run_err = svc_rows[i]["error_msg"]
        run_has_fail = svc_rows[i]["status"] == "fail"
        j = i
        while j < len(svc_rows) and svc_rows[j]["status"] in ("fail", "warning"):
            if svc_rows[j]["error_msg"]:
                run_err = svc_rows[j]["error_msg"]
            if svc_rows[j]["status"] == "fail":
                run_has_fail = True
            j += 1
        if j < len(svc_rows):
            run_end_iso = svc_rows[j]["timestamp"]
            ongoing = False
        else:
            run_end_iso = None  # still bad as of last known check
            ongoing = True
        intervals.append({
            "started_at": run_start_iso,
            "resolved_at": run_end_iso,
            "ongoing": ongoing,
            "start_dt": datetime.fromisoformat(run_start_iso),
            "end_dt": now_utc if ongoing else datetime.fromisoformat(run_end_iso),
            "err": run_err,
            "severity": "fail" if run_has_fail else "warning",
        })
        i = j + 1
    return intervals


def _bucket_intervals(intervals, hour_start, hour_end):
    """Union-merge intervals against [hour_start, hour_end), return bad_seconds."""
    clipped = []
    for iv in intervals:
        s = max(iv["start_dt"], hour_start)
        e = min(iv["end_dt"], hour_end)
        if e > s:
            clipped.append((s, e))
    clipped.sort()
    bad = 0.0
    merged_end = None
    for s, e in clipped:
        if merged_end is not None and s <= merged_end:
            if e > merged_end:
                bad += (e - merged_end).total_seconds()
                merged_end = e
        else:
            bad += (e - s).total_seconds()
            merged_end = e
    return bad


def _hourly_timeline(intervals_by_service, check_timestamps, now_utc, hours=24):
    """Build N hourly buckets from per-service intervals.

    intervals_by_service: dict {service: [interval, ...]} (output of
      _build_intervals per service). For server-wide timeline pass every
      service; for per-service timeline pass a single-entry dict.
    check_timestamps: list of datetime — used only to decide "empty" buckets
      (no checks happened that hour).
    """
    # Pre-bucket check timestamps by hour-offset so we can mark empty hours.
    has_check = [False] * hours
    for ts in check_timestamps:
        delta_hours = (now_utc - ts).total_seconds() / 3600.0
        if 0 <= delta_hours < hours:
            idx_from_end = int(delta_hours)
            has_check[hours - 1 - idx_from_end] = True

    timeline = []
    for h in range(hours - 1, -1, -1):
        hour_start = (now_utc - timedelta(hours=h)).replace(
            minute=0, second=0, microsecond=0
        )
        hour_end = hour_start + timedelta(hours=1)
        idx = hours - 1 - h

        # incidents (runs) intersecting this hour, per service
        incident_runs = []
        all_intervals_for_hour = []
        for svc, ivs in intervals_by_service.items():
            for iv in ivs:
                if iv["end_dt"] <= hour_start or iv["start_dt"] >= hour_end:
                    continue
                all_intervals_for_hour.append(iv)
                downtime = (
                    format_downtime(iv["started_at"], iv["resolved_at"])
                    if iv["resolved_at"] else None
                )
                incident_runs.append({
                    "svc": svc,
                    "started_at": iv["started_at"],
                    "resolved_at": iv["resolved_at"],
                    "downtime": downtime,
                    "err": iv["err"],
                    "severity": iv["severity"],
                })
        incident_runs.sort(key=lambda x: x["started_at"])

        if not has_check[idx] and not incident_runs:
            timeline.append({
                "hour": hour_start.isoformat(),
                "status": "empty",
                "severity": 0.0,
                "incidents": 0,
                "bad_seconds": 0,
            })
            continue

        bad_seconds = _bucket_intervals(all_intervals_for_hour, hour_start, hour_end)
        severity = _severity_curve(bad_seconds) if bad_seconds > 0 else 0.0
        if severity == 0.0:
            bucket_status = "ok"
        elif severity >= 1.0:
            bucket_status = "fail"
        else:
            bucket_status = "warning"

        entry = {
            "hour": hour_start.isoformat(),
            "status": bucket_status,
            "severity": severity,
            "incidents": len(incident_runs),
            "bad_seconds": int(bad_seconds),
        }
        if incident_runs:
            entry["failed"] = incident_runs
        timeline.append(entry)

    return timeline


# --- API Routes ---


@monitoring_bp.route("/api/monitoring/status")
def monitoring_status():
    """Current status of all servers and services.

    One SQL per server for the 24h window (replaces N+1 from earlier). All
    aggregates (current status, severity_24h, server timeline) are computed
    in Python from a single result set.
    """
    config = load_config()
    conn = get_db()
    now_utc = datetime.now(timezone.utc)
    day_cutoff = (now_utc - timedelta(hours=24)).isoformat()
    month_cutoff = (now_utc - timedelta(days=30)).isoformat()

    servers = {}
    for server_name, server_cfg in config.get("servers", {}).items():
        custom_names = [c["name"] for c in server_cfg.get("custom_checks", [])]
        all_services = ["ssh"] + service_names(server_cfg.get("services", [])) + custom_names

        # One SQL: every check this server saw in the 24h window, ASC.
        rows_24h = conn.execute(
            "SELECT timestamp, service, status, response_time_ms, error_msg "
            "FROM checks WHERE server=? AND timestamp > ? ORDER BY timestamp ASC",
            (server_name, day_cutoff),
        ).fetchall()
        by_service = {}
        for r in rows_24h:
            by_service.setdefault(r["service"], []).append(dict(r))

        services_status = {}
        intervals_by_service = {}
        max_response_ms = 0
        has_fail = False
        has_warning = False
        last_check_iso = None
        for svc in all_services:
            svc_rows = by_service.get(svc, [])
            if svc_rows:
                last = svc_rows[-1]
                services_status[svc] = {
                    "status": last["status"],
                    "response_time_ms": last["response_time_ms"],
                    "error": last["error_msg"],
                }
                if last["status"] == "fail":
                    has_fail = True
                elif last["status"] == "warning":
                    has_warning = True
                if last["response_time_ms"] and last["response_time_ms"] > max_response_ms:
                    max_response_ms = last["response_time_ms"]
                if not last_check_iso or last["timestamp"] > last_check_iso:
                    last_check_iso = last["timestamp"]
            else:
                services_status[svc] = {"status": "unknown"}

            # 24h aggregate health: severity averaged across 24 hourly buckets
            # using the same interval-merging machinery as the server-wide
            # uptime bar. Means the dot color and the timeline cells share
            # exactly one metric — no divergence between them.
            intervals = _build_intervals(svc_rows, now_utc)
            intervals_by_service[svc] = intervals
            svc_check_ts = [datetime.fromisoformat(r["timestamp"]) for r in svc_rows]
            svc_timeline = _hourly_timeline(
                {svc: intervals}, svc_check_ts, now_utc, hours=24
            )
            bad_seconds_24h = sum(b["bad_seconds"] for b in svc_timeline)
            severity_24h = sum(b["severity"] for b in svc_timeline) / len(svc_timeline)
            if svc_rows:
                services_status[svc]["bad_seconds_24h"] = int(bad_seconds_24h)
                services_status[svc]["severity_24h"] = round(severity_24h, 4)
                services_status[svc]["checks_24h"] = len(svc_rows)

        # Server-wide 24h uptime bar: same machinery, but with intervals from
        # every service in the dict — bad seconds from any service count once.
        server_check_ts = [datetime.fromisoformat(r["timestamp"]) for r in rows_24h]
        uptime_timeline = _hourly_timeline(
            intervals_by_service, server_check_ts, now_utc, hours=24
        )

        # 30d uptime across ALL services — two cheap COUNT() aggregates.
        # Warnings are excluded from both numerator and denominator (only
        # ok/fail count), so transient degradations (packet-loss, tx-drops)
        # don't penalise the score — only hard service failures do.
        total = conn.execute(
            "SELECT COUNT(*) as cnt FROM checks WHERE server=? "
            "AND status IN ('ok','fail') AND timestamp > ?",
            (server_name, month_cutoff),
        ).fetchone()["cnt"]
        ok_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM checks WHERE server=? "
            "AND status='ok' AND timestamp > ?",
            (server_name, month_cutoff),
        ).fetchone()["cnt"]
        uptime = round((ok_count / total * 100), 1) if total > 0 else 100

        if has_fail:
            srv_status = "fail"
        elif has_warning:
            srv_status = "warning"
        else:
            srv_status = "ok"

        servers[server_name] = {
            "status": srv_status,
            "last_check": last_check_iso,
            "response_time_ms": max_response_ms,
            "services": services_status,
            "uptime_30d": uptime,
            "uptime_checks": total,
            "uptime_ok": ok_count,
            "uptime_timeline": uptime_timeline,
            "host": server_cfg["host"],
        }

    conn.close()

    return jsonify({
        "servers": servers,
        "last_check": _last_check_time,
        "next_check": _next_check_time,
    })


@monitoring_bp.route("/api/monitoring/incidents")
def monitoring_incidents():
    """Incidents grouped by server: active + resolved history under same server card."""
    limit = request.args.get("limit", 30, type=int)
    conn = get_db()

    # Get active incidents
    active_rows = conn.execute(
        "SELECT * FROM incidents WHERE resolved_at IS NULL ORDER BY started_at DESC LIMIT ?",
        (limit,),
    ).fetchall()

    # Group by server
    server_incidents = {}
    seen_servers = set()

    for r in active_rows:
        srv = r["server"]
        seen_servers.add(srv)
        if srv not in server_incidents:
            server_incidents[srv] = {"server": srv, "active": [], "resolved": []}

        error = r["error_msg"]
        if not error:
            check_row = conn.execute(
                "SELECT error_msg FROM checks WHERE server=? AND service=? AND status='fail' ORDER BY timestamp DESC LIMIT 1",
                (r["server"], r["service"]),
            ).fetchone()
            error = check_row["error_msg"] if check_row and check_row["error_msg"] else "Service check failed"

        server_incidents[srv]["active"].append({
            "id": r["id"],
            "service": r["service"],
            "started_at": r["started_at"],
            "error": error,
            "duration": format_downtime(r["started_at"], datetime.now(timezone.utc).isoformat()),
        })

    # Get recently resolved — attach to same server group
    resolved_rows = conn.execute(
        "SELECT * FROM incidents WHERE resolved_at IS NOT NULL ORDER BY resolved_at DESC LIMIT ?",
        (limit,),
    ).fetchall()

    for r in resolved_rows:
        srv = r["server"]
        if srv not in server_incidents:
            server_incidents[srv] = {"server": srv, "active": [], "resolved": []}
        server_incidents[srv]["resolved"].append({
            "id": r["id"],
            "service": r["service"],
            "started_at": r["started_at"],
            "resolved_at": r["resolved_at"],
            "downtime": format_downtime(r["started_at"], r["resolved_at"]),
            "error": r["error_msg"],
        })

    # Convert to list, servers with active incidents first
    incidents = sorted(
        server_incidents.values(),
        key=lambda x: (0 if x["active"] else 1, x["server"]),
    )

    conn.close()
    return jsonify({"incidents": incidents})


@monitoring_bp.route("/api/monitoring/uptime")
def monitoring_uptime():
    """Uptime percentages per server over N days."""
    days = request.args.get("days", 30, type=int)
    config = load_config()
    conn = get_db()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    result = {}
    for server_name in config.get("servers", {}):
        total = conn.execute(
            "SELECT COUNT(*) as cnt FROM checks WHERE server=? AND service='ssh' AND timestamp > ?",
            (server_name, cutoff),
        ).fetchone()["cnt"]
        ok_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM checks WHERE server=? AND service='ssh' AND status='ok' AND timestamp > ?",
            (server_name, cutoff),
        ).fetchone()["cnt"]
        result[server_name] = round((ok_count / total * 100), 1) if total > 0 else 0

    conn.close()
    return jsonify({"days": days, "uptime": result})


@monitoring_bp.route("/api/monitoring/check", methods=["POST"])
def monitoring_check_now():
    """Trigger an immediate check cycle."""
    threading.Thread(target=run_check_cycle, daemon=True).start()
    return jsonify({"status": "started", "message": "Check cycle triggered"})


@monitoring_bp.route("/api/monitoring/resolve", methods=["POST"])
def monitoring_resolve():
    """Manually resolve an open incident."""
    data = request.json or {}
    server = data.get("server")
    service = data.get("service")
    if not server or not service:
        return jsonify({"error": "server and service required"}), 400

    timestamp = datetime.now(timezone.utc).isoformat()
    conn = get_db()
    row = conn.execute(
        "SELECT id, started_at FROM incidents WHERE server=? AND service=? AND resolved_at IS NULL",
        (server, service),
    ).fetchone()

    if not row:
        conn.close()
        return jsonify({"status": "noop", "message": "No open incident found"})

    # Suppress for 1 hour so next check cycle won't reopen
    suppress_until = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    conn.execute(
        "UPDATE incidents SET resolved_at=?, suppressed_until=? WHERE id=?",
        (timestamp, suppress_until, row["id"]),
    )
    conn.commit()
    conn.close()

    downtime = format_downtime(row["started_at"], timestamp)
    logger.info(f"Manually resolved {server}/{service} (downtime: {downtime}, suppressed 1h)")
    return jsonify({"status": "resolved", "downtime": downtime})


@monitoring_bp.route("/api/monitoring/dismiss", methods=["POST"])
def monitoring_dismiss():
    """Dismiss (delete) a resolved incident or all resolved incidents."""
    data = request.json or {}
    incident_id = data.get("id")

    conn = get_db()
    if incident_id:
        conn.execute("DELETE FROM incidents WHERE id=? AND resolved_at IS NOT NULL", (incident_id,))
    else:
        # Dismiss all resolved
        conn.execute("DELETE FROM incidents WHERE resolved_at IS NOT NULL")
    conn.commit()
    conn.close()
    return jsonify({"status": "ok"})


@monitoring_bp.route("/api/monitoring/purge", methods=["POST"])
def monitoring_purge():
    """Purge all check history and incidents. Fresh start."""
    conn = get_db()
    conn.execute("DELETE FROM checks")
    conn.execute("DELETE FROM incidents")
    conn.commit()
    conn.close()
    logger.info("Monitoring data purged")
    return jsonify({"status": "purged"})


@monitoring_bp.route("/api/monitoring/mute", methods=["POST"])
def monitoring_mute():
    """Mute a service — move it from services to muted list in config."""
    data = request.json or {}
    server = data.get("server")
    service = data.get("service")
    if not server or not service:
        return jsonify({"error": "server and service required"}), 400

    config = load_config()
    srv = config.get("servers", {}).get(server)
    if not srv:
        return jsonify({"error": f"Server {server} not found"}), 404

    names = service_names(srv.get("services", []))
    if service not in names:
        return jsonify({"status": "noop", "message": f"{service} not in {server} services"})
    removed = next((s for s in srv.get("services", []) if resolve_service(s)[0] == service), service)
    srv["services"] = [s for s in srv.get("services", []) if resolve_service(s)[0] != service]
    srv["muted"] = srv.get("muted", []) + [removed]

    # Resolve incidents and clean up check history for this service
    timestamp = datetime.now(timezone.utc).isoformat()
    conn = get_db()
    conn.execute(
        "UPDATE incidents SET resolved_at=? WHERE server=? AND service=? AND resolved_at IS NULL",
        (timestamp, server, service),
    )
    conn.execute(
        "DELETE FROM checks WHERE server=? AND service=?",
        (server, service),
    )
    conn.execute(
        "DELETE FROM incidents WHERE server=? AND service=?",
        (server, service),
    )
    conn.commit()
    conn.close()

    # Save config
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)

    logger.info(f"Muted {server}/{service} — history purged")
    return jsonify({"status": "muted", "message": f"{service} removed from {server}"})


@monitoring_bp.route("/api/monitoring/unmute", methods=["POST"])
def monitoring_unmute():
    """Unmute a service — move it back from muted to services."""
    data = request.json or {}
    server = data.get("server")
    service = data.get("service")
    if not server or not service:
        return jsonify({"error": "server and service required"}), 400

    config = load_config()
    srv = config.get("servers", {}).get(server)
    if not srv:
        return jsonify({"error": f"Server {server} not found"}), 404

    muted = srv.get("muted", [])
    if service not in service_names(muted):
        return jsonify({"status": "noop", "message": f"{service} not muted on {server}"})
    entry = next((s for s in muted if resolve_service(s)[0] == service), service)
    srv["muted"] = [s for s in muted if resolve_service(s)[0] != service]
    srv["services"] = srv.get("services", []) + [entry]

    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)

    logger.info(f"Unmuted {server}/{service}")
    return jsonify({"status": "unmuted", "message": f"{service} restored on {server}"})


@monitoring_bp.route("/api/monitoring/muted")
def monitoring_muted_list():
    """List all muted services across servers."""
    config = load_config()
    muted = {}
    for server_name, server_cfg in config.get("servers", {}).items():
        m = server_cfg.get("muted", [])
        if m:
            muted[server_name] = m
    return jsonify({"muted": muted})


@monitoring_bp.route("/api/monitoring/history")
def monitoring_history():
    """Check history for a server, optionally filtered by service.

    When service= is given, also returns a precomputed hourly timeline
    (24 buckets by default) so the frontend never has to bucket locally —
    the bar matches the server-wide bar visually because both come from the
    same _hourly_timeline() machinery.
    """
    server = request.args.get("server")
    service = request.args.get("service")
    hours = request.args.get("hours", 24, type=int)
    if not server:
        return jsonify({"error": "server parameter required"}), 400

    now_utc = datetime.now(timezone.utc)
    cutoff = (now_utc - timedelta(hours=hours)).isoformat()
    conn = get_db()

    timeline = None
    if service:
        # ASC for interval building, then we'll DESC the response copy.
        asc_rows = conn.execute(
            "SELECT timestamp, service, status, response_time_ms, error_msg "
            "FROM checks WHERE server=? AND service=? AND timestamp > ? "
            "ORDER BY timestamp ASC",
            (server, service, cutoff),
        ).fetchall()
        svc_rows = [dict(r) for r in asc_rows]
        intervals = _build_intervals(svc_rows, now_utc)
        check_ts = [datetime.fromisoformat(r["timestamp"]) for r in svc_rows]
        timeline = _hourly_timeline(
            {service: intervals}, check_ts, now_utc, hours=hours
        )
        rows_desc = list(reversed(svc_rows))
    else:
        rows = conn.execute(
            "SELECT timestamp, service, status, response_time_ms, error_msg FROM checks "
            "WHERE server=? AND timestamp > ? ORDER BY timestamp DESC",
            (server, cutoff),
        ).fetchall()
        rows_desc = [dict(r) for r in rows]
    conn.close()

    resp = {
        "server": server,
        "service": service,
        "hours": hours,
        "checks": rows_desc,
    }
    if timeline is not None:
        resp["timeline"] = timeline
    return jsonify(resp)
