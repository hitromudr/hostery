import config_api as c

def test_validate_ok():
    cfg = {"check_interval": 300, "servers": {
        "web": {"host": "example.com", "user": "root",
                "services": ["nginx", {"name": "p", "type": "port", "port": 80}]}}}
    assert c.validate_config(cfg) == []

def test_validate_missing_host():
    cfg = {"servers": {"web": {"user": "root", "services": []}}}
    errs = c.validate_config(cfg)
    assert any("host" in e for e in errs)

def test_validate_bad_check_type():
    cfg = {"servers": {"web": {"host": "h", "user": "u",
           "services": [{"name": "x", "type": "bogus"}]}}}
    errs = c.validate_config(cfg)
    assert any("bogus" in e for e in errs)

def test_redact_telegram_token():
    cfg = {"telegram": {"bot_token": "SECRET", "chat_id": "1"}, "servers": {}}
    red = c.redact(cfg)
    assert red["telegram"]["bot_token"] == ""
    assert cfg["telegram"]["bot_token"] == "SECRET"  # original untouched
