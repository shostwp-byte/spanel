from app.services import sso_tokens


def test_panel_login_token_is_one_shot(monkeypatch, tmp_path):
    monkeypatch.setattr(sso_tokens, "TOKEN_DIR", tmp_path)

    token = sso_tokens.create_panel_login_token("bp123_abcd")

    assert sso_tokens.consume_panel_login_token(token)["username"] == "bp123_abcd"
    assert sso_tokens.consume_panel_login_token(token) is None


def test_phpmyadmin_token_is_not_panel_login(monkeypatch, tmp_path):
    monkeypatch.setattr(sso_tokens, "TOKEN_DIR", tmp_path)

    token = sso_tokens.create_phpmyadmin_token("dbu", "secret", "dbname")

    assert sso_tokens.consume_panel_login_token(token) is None


def test_invalid_panel_login_token_returns_none(monkeypatch, tmp_path):
    monkeypatch.setattr(sso_tokens, "TOKEN_DIR", tmp_path)

    assert sso_tokens.consume_panel_login_token("../bad") is None
