import auth

def test_parse_user_pass():
    mode, creds = auth.parse_auth_env("admin:secret")
    assert mode == "fixed"
    assert creds == ("admin", "secret")

def test_parse_off():
    mode, creds = auth.parse_auth_env("off")
    assert mode == "off"
    assert creds is None

def test_parse_unset_generates():
    mode, creds = auth.parse_auth_env(None)
    assert mode == "generated"
    assert creds[0] == "admin"
    assert len(creds[1]) >= 12

def test_check_credentials():
    assert auth.check_credentials(("admin", "pw"), "admin", "pw") is True
    assert auth.check_credentials(("admin", "pw"), "admin", "nope") is False
