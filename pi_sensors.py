import logging
import sqlite3
import subprocess
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

POWER_DB_PATH = Path(__file__).parent / "config" / "power.db"
THROTTLE_CHECK_INTERVAL = 10  # seconds
_throttle_prev = 0
_voltage_stats = {"min": None, "max": None, "current": 0.0}
_power_watts = 0.0

# Host temperature provider, wired by app.py to the generic sensor.
_temp_provider = lambda: 0.0


def set_temp_provider(fn):
    global _temp_provider
    _temp_provider = fn


def init_power_db():
    conn = sqlite3.connect(str(POWER_DB_PATH), timeout=5)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS power_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            event_type TEXT NOT NULL,
            raw_flags INTEGER NOT NULL,
            temp REAL,
            voltage TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_power_ts ON power_events(timestamp);

        CREATE TABLE IF NOT EXISTS power_readings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            voltage REAL NOT NULL,
            power_watts REAL NOT NULL,
            temp REAL
        );
        CREATE INDEX IF NOT EXISTS idx_readings_ts ON power_readings(timestamp);
    """)
    conn.close()


def get_power_db():
    conn = sqlite3.connect(str(POWER_DB_PATH), timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def get_throttle_raw():
    try:
        result = subprocess.run(["vcgencmd", "get_throttled"],
                                capture_output=True, text=True, timeout=2)
        val = result.stdout.strip().split("=")[-1]
        return int(val, 16)
    except Exception:
        return 0


def get_voltage():
    try:
        result = subprocess.run(["vcgencmd", "measure_volts"],
                                capture_output=True, text=True, timeout=2)
        return result.stdout.strip().split("=")[-1]
    except Exception:
        return ""


def get_voltage_numeric():
    try:
        return float(get_voltage().replace("V", ""))
    except Exception:
        return 0.0


def get_pmic_power():
    try:
        result = subprocess.run(["vcgencmd", "pmic_read_adc"],
                                capture_output=True, text=True, timeout=2)
        lines = result.stdout.strip().split("\n")
        currents = {}
        voltages = {}
        for line in lines:
            line = line.strip()
            if "current" in line:
                parts = line.split()
                rail = parts[0].replace("_A", "")
                currents[rail] = float(parts[1].split("=")[1].rstrip("A"))
            elif "volt" in line:
                parts = line.split()
                rail = parts[0].replace("_V", "")
                voltages[rail] = float(parts[1].split("=")[1].rstrip("V"))
        total = 0.0
        for rail, current in currents.items():
            total += current * voltages.get(rail, 0.0)
        return round(total, 2)
    except Exception:
        return 0.0


def update_voltage_stats():
    global _voltage_stats, _power_watts
    v = get_voltage_numeric()
    _power_watts = get_pmic_power()
    if v > 0:
        _voltage_stats["current"] = v
        now = datetime.now(timezone.utc).isoformat()
        temp = _temp_provider()
        try:
            conn = get_power_db()
            conn.execute(
                "INSERT INTO power_readings (timestamp, voltage, power_watts, temp) VALUES (?,?,?,?)",
                (now, v, _power_watts, temp))
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
            conn.execute("DELETE FROM power_readings WHERE timestamp < ?", (cutoff,))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Power reading write error: {e}")


def get_voltage_stats_24h():
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    try:
        conn = get_power_db()
        row = conn.execute(
            "SELECT MIN(voltage) as v_min, MAX(voltage) as v_max, AVG(voltage) as v_avg, "
            "AVG(power_watts) as p_avg, MAX(power_watts) as p_max, COUNT(*) as cnt "
            "FROM power_readings WHERE timestamp >= ?", (cutoff,)).fetchone()
        events_24h = conn.execute(
            "SELECT COUNT(*) FROM power_events WHERE timestamp >= ? AND event_type IN "
            "('under_voltage', 'under_voltage_at_boot')", (cutoff,)).fetchone()[0]
        conn.close()
        if row and row["cnt"] > 0:
            return {
                "min": round(row["v_min"], 4), "max": round(row["v_max"], 4),
                "avg": round(row["v_avg"], 4), "power_avg": round(row["p_avg"], 2),
                "power_max": round(row["p_max"], 2), "readings": row["cnt"],
                "undervoltage_events_24h": events_24h,
            }
    except Exception as e:
        logger.error(f"Voltage stats query error: {e}")
    return None


THROTTLE_FLAGS = {0: "under_voltage", 1: "arm_freq_capped", 2: "throttled", 3: "soft_temp_limit"}


def parse_throttle_active(raw):
    return [name for bit, name in THROTTLE_FLAGS.items() if raw & (1 << bit)]


def throttle_monitor_loop():
    global _throttle_prev
    while True:
        try:
            update_voltage_stats()
            raw = get_throttle_raw()
            active = raw & 0xF
            prev_active = _throttle_prev & 0xF
            if active != prev_active:
                now = datetime.now(timezone.utc).isoformat()
                temp = _temp_provider()
                voltage = get_voltage()
                power = _power_watts
                meta = f"{voltage} {power}W"
                conn = get_power_db()
                for bit, name in THROTTLE_FLAGS.items():
                    was_on = prev_active & (1 << bit)
                    is_on = active & (1 << bit)
                    if is_on and not was_on:
                        conn.execute(
                            "INSERT INTO power_events (timestamp, event_type, raw_flags, temp, voltage) "
                            "VALUES (?,?,?,?,?)", (now, name, raw, temp, meta))
                        logger.warning(f"Power event: {name} (flags=0x{raw:x}, temp={temp}, {meta})")
                    elif was_on and not is_on:
                        conn.execute(
                            "INSERT INTO power_events (timestamp, event_type, raw_flags, temp, voltage) "
                            "VALUES (?,?,?,?,?)", (now, f"{name}_resolved", raw, temp, meta))
                        logger.info(f"Power event resolved: {name} (flags=0x{raw:x})")
                conn.commit()
                conn.close()
            _throttle_prev = raw
        except Exception as e:
            logger.error(f"Throttle monitor error: {e}")
        time.sleep(THROTTLE_CHECK_INTERVAL)


def record_boot_throttle_events():
    raw = get_throttle_raw()
    if raw == 0:
        return
    try:
        import psutil
        boot_time = datetime.fromtimestamp(psutil.boot_time(), tz=timezone.utc)
    except Exception:
        return
    conn = get_power_db()
    existing = conn.execute(
        "SELECT COUNT(*) FROM power_events WHERE event_type LIKE '%_at_boot' AND timestamp >= ?",
        (boot_time.isoformat(),)).fetchone()[0]
    if existing > 0:
        conn.close()
        return
    now = boot_time.isoformat()
    temp = _temp_provider()
    voltage = get_voltage()
    power = get_pmic_power()
    boot_flags = {16: "under_voltage", 17: "arm_freq_capped", 18: "throttled", 19: "soft_temp_limit"}
    for bit, name in boot_flags.items():
        if raw & (1 << bit):
            conn.execute(
                "INSERT INTO power_events (timestamp, event_type, raw_flags, temp, voltage) VALUES (?,?,?,?,?)",
                (now, f"{name}_at_boot", raw, temp, f"{voltage} {power}W"))
            logger.warning(f"Boot power event: {name} (flags=0x{raw:x})")
    conn.commit()
    conn.close()


def start_throttle_monitor():
    record_boot_throttle_events()
    t = threading.Thread(target=throttle_monitor_loop, daemon=True)
    t.start()
    logger.info("Throttle monitor started")


def throttle_snapshot():
    raw = get_throttle_raw()
    return {
        "raw": raw,
        "active": parse_throttle_active(raw),
        "ever_under_voltage": bool(raw & (1 << 16)),
        "ever_throttled": bool(raw & (1 << 18)),
        "voltage": get_voltage(),
        "power_watts": _power_watts,
        "stats_24h": get_voltage_stats_24h(),
    }


def power_timeline(hours=24):
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    conn = get_power_db()
    rows = conn.execute(
        "SELECT timestamp, event_type, raw_flags, temp, voltage FROM power_events "
        "WHERE timestamp >= ? ORDER BY timestamp", (cutoff,)).fetchall()
    total = conn.execute("SELECT COUNT(*) FROM power_events").fetchone()[0]
    conn.close()
    now = datetime.now(timezone.utc)
    current_hour = now.replace(minute=0, second=0, microsecond=0)
    timeline = []
    for i in range(hours):
        block_start = current_hour - timedelta(hours=hours - 1 - i)
        block_end = block_start + timedelta(hours=1)
        block_events = [dict(r) for r in rows
                        if block_start.isoformat() <= r["timestamp"] < block_end.isoformat()]
        bad_count = sum(1 for e in block_events if not e["event_type"].endswith("_resolved"))
        timeline.append({
            "hour": block_start.strftime("%H:00"),
            "timestamp": block_start.isoformat(),
            "events": bad_count,
            "details": block_events,
        })
    recent = [dict(r) for r in rows[-20:]]
    return {"timeline": timeline, "recent": recent, "total_all_time": total}
