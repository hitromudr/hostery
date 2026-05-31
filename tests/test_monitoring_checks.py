import monitoring as m

def test_resolve_bare_string_is_systemctl():
    name, check = m.resolve_service("nginx")
    assert name == "nginx"
    assert check == {"type": "systemctl", "unit": "nginx"}

def test_resolve_dict_keeps_fields():
    name, check = m.resolve_service({"name": "socks", "type": "port", "port": 8088})
    assert name == "socks"
    assert check["type"] == "port"
    assert check["port"] == 8088

def test_service_names_mixed_list():
    services = ["nginx", {"name": "socks", "type": "port", "port": 8088}]
    assert m.service_names(services) == ["nginx", "socks"]

def test_eval_expect_operators():
    assert m._eval_expect("> 0", 5, "5") is True
    assert m._eval_expect("== 1", 1, "1") is True
    assert m._eval_expect("<= 20", 25, "25") is False

def test_build_check_command_port():
    cmd = m.build_check_command({"type": "port", "port": 8088})
    assert "8088" in cmd

def test_build_check_command_http():
    cmd = m.build_check_command({"type": "http", "url": "http://127.0.0.1:8080/health"})
    assert "http://127.0.0.1:8080/health" in cmd
    assert "http_code" in cmd
