"""Built-in `disk` row: 80% of the fullest filesystem warns, 95% fails.

Owner's rule (2026-11-26): every node in the fleet reports disk usage without
anyone adding a per-node custom check. Above 80% is a warning (yellow, no
incident — same treatment as `direct`); above 95% is a failure (red, incident,
Telegram alert). Before this the fleet carried a hand-copied `disk-root`
custom check on some nodes only, so a node added to the fleet silently had no
disk watch at all.
"""

import json
import subprocess
import shutil
from pathlib import Path

import pytest

import config_api
import monitoring as m

ROOT = Path(__file__).resolve().parent.parent


# --- thresholds ---


@pytest.mark.parametrize("pct,status", [
    (0, "ok"), (50, "ok"), (79, "ok"), (80, "ok"),
    (81, "warning"), (90, "warning"), (95, "warning"),
    (96, "fail"), (99, "fail"), (100, "fail"),
])
def test_thresholds(pct, status):
    """Strictly above the threshold: exactly 80 and 95 are still inside it."""
    assert m.disk_status(pct, "/")[0] == status


def test_error_names_the_percent_and_the_mount():
    assert "96%" in m.disk_status(96, "/var")[1]
    assert "/var" in m.disk_status(96, "/var")[1]
    assert m.disk_status(50, "/")[1] is None


# --- parsing ---


def test_parse_disk_usage_reads_percent_and_mount():
    assert m.parse_disk_usage("83\t/\n") == (83, "/")
    assert m.parse_disk_usage("91 /home\n") == (91, "/home")


@pytest.mark.parametrize("output", ["", "garbage", "no data here"])
def test_parse_disk_usage_refuses_junk(output):
    with pytest.raises(ValueError):
        m.parse_disk_usage(output)


def test_a_command_that_cannot_read_the_disk_is_a_warning_not_a_silent_green(monkeypatch):
    """An unreadable `df` must not look clean: silence is the failure mode
    that let a full disk go unnoticed on 12.10.2026."""
    monkeypatch.setattr(m, "_get_ssh_client", lambda *a, **k: object())
    monkeypatch.setattr(m, "_close_ssh_client", lambda client: None)
    monkeypatch.setattr(m, "_ssh_exec", lambda client, cmd, timeout=8: "")
    results = {r[0]: r for r in m.check_server("S", {"host": "h", "user": "u"}, {"servers": {}})}
    assert results["disk"][1] == "warning"
    assert "disk usage unknown" in results["disk"][2]


# --- the command itself: which filesystems count ---


def _disk_command_output(df_text, mounts_text, tmp_path):
    """Run DISK_COMMAND against a literal /proc/mounts via awk only.

    The awk body is the part under test — `df` itself is the host's. Feeding
    both tables as files keeps the case deterministic on any machine.
    """
    awk = m.DISK_COMMAND.split("| awk '", 1)[1].rsplit("' /proc/mounts -", 1)[0]
    df = tmp_path / "df.txt"
    mounts = tmp_path / "mounts.txt"
    df.write_text(df_text)
    mounts.write_text(mounts_text)
    out = subprocess.run(["awk", awk, str(mounts), str(df)],
                         capture_output=True, text=True, check=True)
    # Only the trailing newline: the tab between percent and mount matters,
    # and with no writable filesystem the mount half is legitimately empty.
    return out.stdout.rstrip("\n")


DF = """Filesystem 1024-blocks Used Available Capacity Mounted on
/dev/sda1 100 40 60 40% /
/dev/sda2 100 90 10 90% /var
/dev/loop0 100 100 0 100% /mnt/iso
"""
MOUNTS = """/dev/sda1 / ext4 rw,relatime 0 0
/dev/sda2 /var ext4 rw,relatime 0 0
/dev/loop0 /mnt/iso iso9660 ro,relatime 0 0
"""


def test_a_full_read_only_image_does_not_red_the_node(tmp_path):
    """The Pi keeps game ISOs under /mnt: read-only, always 100%, cannot fill."""
    if not shutil.which("awk"):
        pytest.skip("awk is not installed")
    assert _disk_command_output(DF, MOUNTS, tmp_path) == "90\t/var"


def test_a_read_only_root_does_not_count(tmp_path):
    if not shutil.which("awk"):
        pytest.skip("awk is not installed")
    df = "Filesystem 1024-blocks Used Available Capacity Mounted on\n/dev/sda1 100 99 1 99% /\n"
    mounts = "/dev/sda1 / ext4 ro,relatime 0 0\n"
    assert _disk_command_output(df, mounts, tmp_path) == "0\t"


def test_a_mount_point_with_a_space_does_not_break_the_match(tmp_path):
    """`df` pads the mount column, so a path with a space shifts $6."""
    if not shutil.which("awk"):
        pytest.skip("awk is not installed")
    df = ("Filesystem 1024-blocks Used Available Capacity Mounted on\n"
          "/dev/sda3 100 99 1 99% /media/My Book\n"
          "/dev/sda1 100 20 80 20% /\n")
    mounts = "/dev/sda3 /media/My Book ext4 rw,relatime 0 0\n/dev/sda1 / ext4 rw,relatime 0 0\n"
    assert _disk_command_output(df, mounts, tmp_path) == "99\t/media/My Book"


def test_a_read_only_mount_listed_after_a_writable_one_still_counts_as_ro(tmp_path):
    """The /proc/mounts pass must not depend on order of the two files."""
    if not shutil.which("awk"):
        pytest.skip("awk is not installed")
    df = ("Filesystem 1024-blocks Used Available Capacity Mounted on\n"
          "/dev/sdb1 100 99 1 99% /data\n")
    mounts = "/dev/sdb1 /data ext4 ro,nosuid 0 0\n"
    assert _disk_command_output(df, mounts, tmp_path) == "0\t"


# --- wiring into check_server and the cycle ---


SERVER = {"host": "x", "user": "y", "disk_check": True}


def _fake_ssh(monkeypatch, output):
    monkeypatch.setattr(m, "_get_ssh_client", lambda *a, **k: object())
    monkeypatch.setattr(m, "_close_ssh_client", lambda client: None)
    monkeypatch.setattr(m, "_ssh_exec", lambda client, cmd, timeout=8: output)


def test_check_server_reports_disk_with_the_value(monkeypatch):
    _fake_ssh(monkeypatch, "91 /")
    results = {r[0]: r for r in m.check_server("S", SERVER, {"servers": {}})}
    assert results["disk"][1] == "warning"
    assert results["disk"][4] == 91  # the percent is kept as the plotted value


def test_check_server_over_95_is_a_fail(monkeypatch):
    _fake_ssh(monkeypatch, "97 /home")
    results = {r[0]: r for r in m.check_server("S", SERVER, {"servers": {}})}
    assert results["disk"][1] == "fail"
    assert "/home" in results["disk"][2]


def test_disk_row_is_on_by_default():
    assert m.disk_check_enabled({"host": "h"}) is True
    assert m.DISK_CHECK_NAME in m.configured_services({"host": "h"})


def test_disk_row_can_be_switched_off_per_node():
    cfg = {"host": "h", "disk_check": False}
    assert m.disk_check_enabled(cfg) is False
    assert m.DISK_CHECK_NAME not in m.configured_services(cfg)


def test_a_node_with_its_own_disk_check_keeps_it():
    """Two rows named `disk` would collide on (timestamp, server, service)
    and one would silently overwrite the other."""
    cfg = {"host": "h", "custom_checks": [{"name": "disk", "command": "df", "expect": "<= 85"}]}
    assert m.disk_check_enabled(cfg) is False
    assert m.configured_services(cfg).count("disk") == 1


def test_switched_off_node_reports_disk_off(monkeypatch):
    def boom(*a, **k):
        raise TimeoutError("timed out")
    monkeypatch.setattr(m, "_get_ssh_client", boom)
    monkeypatch.setattr(m.time, "sleep", lambda *_: None)
    cfg = {"host": "127.0.0.1", "user": "u", "ssh_port": 2203, "switched_off_ok": True}
    results = {r[0]: r[1] for r in m.check_server("LAPTOP", cfg, {"ssh_timeout": 1, "servers": {}})}
    assert results == {"ssh": "off", "disk": "off"}


def test_disk_warning_opens_no_incident_but_a_fail_does(tmp_path, monkeypatch):
    from flask import Flask

    monkeypatch.setattr(m, "DB_PATH", tmp_path / "mon.db")
    config = {"check_interval": 300, "servers": {"S": {"host": "x", "user": "y", "alerts": False}}}
    monkeypatch.setattr(m, "load_config", lambda: config)
    sent = []
    monkeypatch.setattr(m, "send_telegram", lambda cfg, msg: sent.append(msg))

    monkeypatch.setattr(m, "check_server", lambda n, c, conf: [
        ("ssh", "ok", None, 10), ("disk", "warning", "Disk 91% used on /, over 80%", 20, 91)])
    m.init_db()
    m.run_check_cycle()
    conn = m.get_db()
    assert conn.execute("SELECT COUNT(*) c FROM incidents").fetchone()["c"] == 0
    rows = conn.execute("SELECT status, value FROM checks WHERE service='disk'").fetchall()
    conn.close()
    assert (rows[0]["status"], rows[0]["value"]) == ("warning", 91)
    assert sent == []

    monkeypatch.setattr(m, "check_server", lambda n, c, conf: [
        ("ssh", "ok", None, 10), ("disk", "fail", "Disk 97% used on /, over 95%", 20, 97)])
    m.run_check_cycle()
    conn = m.get_db()
    inc = conn.execute("SELECT service FROM incidents WHERE resolved_at IS NULL").fetchall()
    conn.close()
    assert [r["service"] for r in inc] == ["disk"]


# --- config validation and mute/unmute ---


def test_config_rejects_a_non_boolean_disk_check():
    base = {"servers": {"A": {"host": "h", "user": "u", "disk_check": "yes"}}}
    assert config_api.validate_config(base) == ["A: disk_check must be true or false"]
    base["servers"]["A"]["disk_check"] = False
    assert config_api.validate_config(base) == []


def _config_on_disk(tmp_path, monkeypatch, srv):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"servers": {"S": srv}}))
    monkeypatch.setattr(m, "CONFIG_PATH", path)
    monkeypatch.setattr(m, "DB_PATH", tmp_path / "mon.db")
    m.init_db()
    return path


def test_muting_the_builtin_disk_row_switches_it_off(tmp_path, monkeypatch):
    from flask import Flask

    path = _config_on_disk(tmp_path, monkeypatch, {"host": "h", "user": "u"})
    with Flask(__name__).test_request_context("/", json={"server": "S", "service": "disk"}):
        resp = m.monitoring_mute().get_json()
    saved = json.loads(path.read_text())["servers"]["S"]
    assert resp["status"] == "muted"
    assert saved["disk_check"] is False
    assert saved["muted"] == ["disk"]
    # The row is gone from the API, so the card stops showing a grey disk tile.
    assert "disk" not in m.configured_services(saved)


def test_unmuting_the_builtin_disk_row_turns_it_back_on(tmp_path, monkeypatch):
    from flask import Flask

    path = _config_on_disk(tmp_path, monkeypatch,
                           {"host": "h", "user": "u", "disk_check": False, "muted": ["disk"]})
    with Flask(__name__).test_request_context("/", json={"server": "S", "service": "disk"}):
        resp = m.monitoring_unmute().get_json()
    saved = json.loads(path.read_text())["servers"]["S"]
    assert resp["status"] == "unmuted"
    assert "disk_check" not in saved
    assert saved.get("muted") == []
    assert m.disk_check_enabled(saved) is True


def test_muting_a_real_service_still_moves_it(tmp_path, monkeypatch):
    """The built-in branch must not swallow ordinary services."""
    from flask import Flask

    path = _config_on_disk(tmp_path, monkeypatch,
                           {"host": "h", "user": "u", "services": ["nginx", "disk"]})
    saved_before = json.loads(path.read_text())["servers"]["S"]
    assert m.disk_check_enabled(saved_before) is False  # its own `disk` service wins
    with Flask(__name__).test_request_context("/", json={"server": "S", "service": "nginx"}):
        m.monitoring_mute()
    saved = json.loads(path.read_text())["servers"]["S"]
    assert saved["services"] == ["disk"]
    assert saved["muted"] == ["nginx"]
