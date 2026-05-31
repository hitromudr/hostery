import monitoring as m


def test_run_check_cycle_empty_fleet_no_crash(tmp_path, monkeypatch):
    # Point DB at a temp file so the cycle can open/init it without touching real data.
    db = tmp_path / "mon.db"
    monkeypatch.setattr(m, "DB_PATH", db)
    monkeypatch.setattr(m, "load_config", lambda: {"check_interval": 300, "servers": {}})
    m.init_db()
    m._next_check_time = None
    m.run_check_cycle()  # must not raise on an empty fleet
    assert m._next_check_time is not None
