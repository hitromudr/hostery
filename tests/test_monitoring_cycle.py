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
