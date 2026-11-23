"""Web UI shared-secret authentication."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import web_server
from config import Settings
from web.auth import ensure_web_auth_ready, load_web_auth_token


def _reset_web_globals() -> None:
    web_server._settings = None
    web_server._agent_stack = None
    web_server._pipeline = None
    web_server._log_store = None
    web_server._command_store = None
    import web.auth as auth_mod

    auth_mod._service = None


def _client_with_token(monkeypatch: pytest.MonkeyPatch, tmp_path, token: str) -> tuple[TestClient, str]:
    monkeypatch.setenv("WEB_AUTH_ENABLED", "true")
    monkeypatch.setenv("WEB_AUTH_TOKEN", token)
    _reset_web_globals()
    return TestClient(web_server.create_app()), token


@pytest.fixture
def auth_token() -> str:
    return "test-web-auth-token-" + ("x" * 24)


def test_refuse_start_without_token(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEB_AUTH_ENABLED", "true")
    monkeypatch.delenv("WEB_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("WEB_AUTH_TOKEN_FILE", str(tmp_path / "missing-token.txt"))
    with pytest.raises(SystemExit):
        ensure_web_auth_ready(Settings.from_env())


def test_protected_api_401_without_credentials(auth_token: str, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    client, _ = _client_with_token(monkeypatch, tmp_path, auth_token)
    assert client.get("/api/config").status_code == 401
    res = client.get("/", follow_redirects=False)
    assert res.status_code == 302
    assert res.headers["location"] == "/login"


def test_bearer_and_cookie_login(auth_token: str, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    client, tok = _client_with_token(monkeypatch, tmp_path, auth_token)
    assert client.get("/api/health").json().get("auth_required") is True
    bad = client.post("/api/auth/login", json={"token": "wrong"})
    assert bad.status_code == 401
    ok = client.post("/api/auth/login", json={"token": tok})
    assert ok.status_code == 200
    cookie = ok.cookies.get("sahir_session")
    assert cookie
    authed = TestClient(web_server.create_app())
    authed.cookies.set("sahir_session", cookie)
    assert authed.get("/api/config").status_code == 200
    bearer = TestClient(web_server.create_app())
    bearer.headers["Authorization"] = f"Bearer {tok}"
    assert bearer.get("/api/activity").status_code == 200


def test_login_query_sets_cookie_and_strips_url(auth_token: str, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    client, tok = _client_with_token(monkeypatch, tmp_path, auth_token)
    res = client.get(f"/login?token={tok}", follow_redirects=False)
    assert res.status_code == 302
    assert res.headers["location"] == "/"
    assert res.cookies.get("sahir_session")


def test_logout_clears_session(auth_token: str, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    client, tok = _client_with_token(monkeypatch, tmp_path, auth_token)
    login = client.post("/api/auth/login", json={"token": tok})
    client.cookies.update(login.cookies)
    out = client.post("/api/auth/logout")
    assert out.status_code == 200
    assert client.get("/api/config").status_code == 401


def test_health_minimal_when_unauthenticated(auth_token: str, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    client, tok = _client_with_token(monkeypatch, tmp_path, auth_token)
    body = client.get("/api/health").json()
    assert body == {"status": "ok", "auth_required": True, "auth_configured": True}
    client.headers["Authorization"] = f"Bearer {tok}"
    full = client.get("/api/health").json()
    assert "ollama" in full


def test_fail_closed_when_enabled_without_token(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEB_AUTH_ENABLED", "true")
    monkeypatch.delenv("WEB_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("WEB_AUTH_TOKEN_FILE", str(tmp_path / "missing-token.txt"))
    _reset_web_globals()
    web_server._settings = Settings.from_env()
    client = TestClient(web_server.create_app())
    blocked = client.get("/api/config")
    assert blocked.status_code == 503
    assert "generate_web_token" in blocked.json()["detail"]
    assert client.get("/", follow_redirects=False).status_code == 302
    login = client.get("/login")
    assert login.status_code == 200
    assert "generate_web_token" in login.text
    st = client.get("/api/auth/status").json()
    assert st["auth_required"] and not st["configured"]
    assert client.post("/api/auth/login", json={"token": "anything"}).status_code == 503


def test_explicit_disable_allows_api(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEB_AUTH_ENABLED", "false")
    _reset_web_globals()
    web_server._settings = Settings.from_env()
    client = TestClient(web_server.create_app())
    assert client.get("/api/health").status_code == 200
    assert client.get("/login").status_code == 404


def test_sse_and_ws_require_auth(auth_token: str, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    client, tok = _client_with_token(monkeypatch, tmp_path, auth_token)
    assert client.get("/api/activity/stream").status_code == 401
    bearer = TestClient(web_server.create_app())
    bearer.headers["Authorization"] = f"Bearer {tok}"
    with bearer.stream("GET", "/api/activity/stream") as res:
        assert res.status_code == 200
    with pytest.raises(Exception):
        with client.websocket_connect("/ws/voice"):
            pass
    with bearer.websocket_connect("/ws/voice") as ws:
        ws.close()


def test_login_rate_limit(auth_token: str, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    client, _ = _client_with_token(monkeypatch, tmp_path, auth_token)
    for _ in range(10):
        client.post("/api/auth/login", json={"token": "nope"})
    blocked = client.post("/api/auth/login", json={"token": "nope"})
    assert blocked.status_code == 429


def test_load_token_from_file(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "tok.txt"
    path.write_text("file-token-" + "a" * 20)
    monkeypatch.delenv("WEB_AUTH_TOKEN", raising=False)
    settings = Settings(web_auth_token_file=path)
    assert load_web_auth_token(settings) == path.read_text().strip()