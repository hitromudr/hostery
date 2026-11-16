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


def test_alerts_default_to_on():
    """Узел, о котором ничего не сказано, звонит: молчание по умолчанию — это
    мониторинг, о котором никто не узнает."""
    config = {"servers": {"HITRO": {"host": "203.0.113.1"}}}
    assert m.alerts_enabled(config, "HITRO") is True
    # Незнакомое имя тоже True: отсутствие записи не повод глушить.
    assert m.alerts_enabled(config, "NOBODY") is True


def test_alerts_can_be_turned_off_for_one_server():
    config = {"servers": {"MICROPC": {"host": "127.0.0.1", "alerts": False}}}
    assert m.alerts_enabled(config, "MICROPC") is False


def test_a_workstation_going_down_records_the_incident_and_stays_quiet(tmp_path, monkeypatch):
    """Инцидент пишется, звонка нет.

    Это и есть разница между «наблюдать» и «звонить»: рабочая станция
    выключается по воле владельца, но её история нужна — по ней видно, когда
    машина была на связи. Проверяется на полном цикле, а не на функции: звонок
    зовётся из того же места, где обновляются инциденты, и разъехаться они могут
    только вместе.
    """
    db = tmp_path / "mon.db"
    monkeypatch.setattr(m, "DB_PATH", db)
    config = {
        "check_interval": 300,
        "servers": {"MICROPC": {"host": "127.0.0.1", "alerts": False, "services": ["rnsd"]}},
    }
    monkeypatch.setattr(m, "load_config", lambda: config)

    called = []
    monkeypatch.setattr(m, "send_telegram", lambda cfg, msg: called.append(msg))
    # Машина «выключена»: подключение не удаётся.
    monkeypatch.setattr(m, "check_server", lambda name, cfg, conf: [("ssh", "fail", "down", 0)])

    m.init_db()
    m._next_check_time = None
    m.run_check_cycle()

    conn = m.get_db()
    rows = conn.execute("SELECT status FROM checks WHERE server='MICROPC'").fetchall()
    incidents = conn.execute("SELECT service FROM incidents WHERE server='MICROPC'").fetchall()
    conn.close()

    assert rows, "проверка не записана — наблюдения нет вовсе"
    assert incidents, "инцидент не открыт — история не покажет, что машина пропадала"
    assert called == [], f"звонок ушёл, хотя alerts=false: {called}"


def test_removing_check_from_config_closes_open_incident_without_recovery_alert(tmp_path, monkeypatch):
    """When a check is retired or moved to another node (e.g. wg0-peers moved
    from HITRO to MAC), its open incident on the old node must close on the
    next cycle without sending a 'recovered' alert, instead of hanging red in
    Active Incidents forever while the server tile is green.
    """
    db = tmp_path / "mon.db"
    monkeypatch.setattr(m, "DB_PATH", db)
    config = {
        "check_interval": 300,
        "servers": {
            "HITRO": {
                "host": "185.250.47.112",
                "services": ["nginx"],
                "custom_checks": [{"name": "cas-exit-node", "command": "echo 1", "expect": "> 0"}],
            }
        },
    }
    monkeypatch.setattr(m, "load_config", lambda: config)

    called = []
    monkeypatch.setattr(m, "send_telegram", lambda cfg, msg: called.append(msg))
    monkeypatch.setattr(
        m,
        "check_server",
        lambda name, cfg, conf: [
            ("ssh", "ok", None, 10),
            ("nginx", "ok", None, 12),
            ("cas-exit-node", "ok", None, 15, 1),
        ],
    )

    m.init_db()
    conn = m.get_db()
    conn.execute(
        "INSERT INTO incidents (server, service, started_at, error_msg) "
        "VALUES ('HITRO', 'wg0-peers', '2026-11-16T15:09:16+00:00', 'got 0 (expected > 0)')"
    )
    conn.execute(
        "INSERT INTO incidents (server, service, started_at, error_msg) "
        "VALUES ('RETIRED', 'ssh', '2026-11-04T10:00:00+00:00', 'timed out')"
    )
    conn.commit()
    conn.close()

    m.run_check_cycle()

    conn = m.get_db()
    open_inc = conn.execute(
        "SELECT server, service FROM incidents WHERE resolved_at IS NULL"
    ).fetchall()
    closed_inc = conn.execute(
        "SELECT server, service, resolved_at FROM incidents WHERE resolved_at IS NOT NULL ORDER BY server"
    ).fetchall()
    conn.close()

    assert [dict(r) for r in open_inc] == []
    assert [(r["server"], r["service"], r["resolved_at"]) for r in closed_inc] == [
        ("HITRO", "wg0-peers", m._last_check_time),
        ("RETIRED", "ssh", m._last_check_time),
    ]
    assert called == []


def test_incidents_endpoint_closes_orphaned_incidents_immediately(tmp_path, monkeypatch):
    """GET /api/monitoring/incidents must not return an active incident for a
    check removed from config.json even before the next 5-minute cycle runs.
    """
    from flask import Flask

    db = tmp_path / "mon.db"
    monkeypatch.setattr(m, "DB_PATH", db)
    monkeypatch.setattr(
        m,
        "load_config",
        lambda: {"servers": {"HITRO": {"host": "185.250.47.112", "services": ["nginx"]}}},
    )
    m.init_db()
    conn = m.get_db()
    conn.execute(
        "INSERT INTO incidents (server, service, started_at, error_msg) "
        "VALUES ('HITRO', 'wg0-peers', '2026-11-16T15:09:16+00:00', 'got 0 (expected > 0)')"
    )
    conn.commit()
    conn.close()

    with Flask(__name__).test_request_context("/api/monitoring/incidents"):
        data = m.monitoring_incidents().get_json()
        assert data["incidents"] == [
            {
                "server": "HITRO",
                "active": [],
                "resolved": [
                    {
                        "id": 1,
                        "service": "wg0-peers",
                        "started_at": "2026-11-16T15:09:16+00:00",
                        "resolved_at": data["incidents"][0]["resolved"][0]["resolved_at"],
                        "downtime": data["incidents"][0]["resolved"][0]["downtime"],
                        "error": "got 0 (expected > 0)",
                    }
                ],
            }
        ]



