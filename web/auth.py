"""Shared-secret authentication for the web UI and API."""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import sys
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware

from config import Settings

logger = logging.getLogger(__name__)

SESSION_COOKIE = "sahir_session"
SESSION_MAX_AGE_S = 30 * 86400
SETUP_MESSAGE = "Web auth token not configured — run scripts/generate_web_token.sh"
_PUBLIC_GET = frozenset({"/login", "/api/health", "/api/auth/status"})
_PUBLIC_POST = frozenset({"/api/auth/login", "/api/auth/logout"})

_service: "WebAuth | None" = None


def default_token_file() -> Path:
    raw = os.getenv("WEB_AUTH_TOKEN_FILE", "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".config" / "jahash-secrets" / "sahir-web-token.txt"


def load_web_auth_token(settings: Settings) -> str | None:
    env = os.getenv("WEB_AUTH_TOKEN", "").strip()
    if env:
        return env
    path = settings.web_auth_token_file
    try:
        if path.is_file():
            token = path.read_text(encoding="utf-8").strip()
            return token or None
    except OSError as exc:
        logger.warning("Could not read web auth token file %s: %s", path, exc)
    return None


def ensure_web_auth_ready(settings: Settings) -> None:
    if not settings.web_auth_enabled:
        return
    token = load_web_auth_token(settings)
    if not token or len(token) < 16:
        path = settings.web_auth_token_file
        print(
            "WEB_AUTH_ENABLED is true but no web auth token is configured.\n"
            "Create one with: bash scripts/generate_web_token.sh\n"
            f"Expected file: {path}\n"
            "Or set WEB_AUTH_TOKEN for scripts only (not in committed .env).\n"
            "To run without auth (trusted LAN only): WEB_AUTH_ENABLED=false",
            file=sys.stderr,
        )
        raise SystemExit(1)


def init_web_auth(settings: Settings | None = None) -> WebAuth:
    global _service
    settings = settings or Settings.from_env()
    auth_required = bool(settings.web_auth_enabled)
    token = load_web_auth_token(settings) if auth_required else None
    configured = bool(token and len(token) >= 16)
    misconfigured = auth_required and not configured
    if misconfigured:
        logger.error("%s (fail-closed: protected routes blocked)", SETUP_MESSAGE)
    secure = bool(settings.web_ssl_cert and settings.web_ssl_key)
    _service = WebAuth(
        auth_required=auth_required,
        misconfigured=misconfigured,
        configured=configured,
        token=token or "",
        secure_cookies=secure,
    )
    return _service


def get_web_auth() -> WebAuth | None:
    return _service


class LoginBody(BaseModel):
    token: str = Field(..., min_length=1, max_length=512)


class LoginRateLimiter:
    def __init__(self, *, max_failures: int = 8, lockout_s: float = 300.0) -> None:
        self._max = max_failures
        self._lockout_s = lockout_s
        self._lock = threading.Lock()
        self._fails: dict[str, tuple[int, float]] = {}

    @staticmethod
    def _key(request: Request) -> str:
        return request.client.host if request.client else "unknown"

    def allowed(self, request: Request) -> bool:
        key = self._key(request)
        now = time.monotonic()
        with self._lock:
            _, until = self._fails.get(key, (0, 0.0))
            if until and now < until:
                return False
            if until and now >= until:
                self._fails.pop(key, None)
        return True

    def remaining_lockout(self, request: Request) -> float:
        key = self._key(request)
        now = time.monotonic()
        with self._lock:
            _, until = self._fails.get(key, (0, 0.0))
            return max(0.0, until - now) if until else 0.0

    def record_failure(self, request: Request) -> None:
        key = self._key(request)
        now = time.monotonic()
        with self._lock:
            count, _ = self._fails.get(key, (0, 0.0))
            count += 1
            until = now + self._lockout_s if count >= self._max else 0.0
            self._fails[key] = (count, until)

    def record_success(self, request: Request) -> None:
        with self._lock:
            self._fails.pop(self._key(request), None)


class WebAuth:
    """auth_required: WEB_AUTH_ENABLED. enabled: enforcing credentials (required + configured)."""

    def __init__(
        self,
        *,
        auth_required: bool,
        misconfigured: bool,
        configured: bool,
        token: str,
        secure_cookies: bool,
    ) -> None:
        self.auth_required = auth_required
        self.misconfigured = misconfigured
        self.configured = configured
        self.enabled = auth_required and configured and not misconfigured
        self.token = token
        self.secure_cookies = secure_cookies
        self._limiter = LoginRateLimiter()

    def is_public_path(self, path: str, method: str) -> bool:
        method = method.upper()
        if path in _PUBLIC_GET and method == "GET":
            return True
        return path in _PUBLIC_POST and method == "POST"

    def verify_bearer(self, value: str) -> bool:
        if not self.enabled or not self.token:
            return False
        return secrets.compare_digest(value.strip(), self.token)

    def issue_session_value(self) -> str:
        exp = int(time.time()) + SESSION_MAX_AGE_S
        nonce = secrets.token_urlsafe(16)
        payload = f"{exp}.{nonce}"
        sig = hmac.new(self.token.encode(), payload.encode(), hashlib.sha256).hexdigest()
        return f"{payload}.{sig}"

    def verify_session_cookie(self, value: str | None) -> bool:
        if not self.enabled or not self.token or not value:
            return False
        parts = value.split(".")
        if len(parts) != 3:
            return False
        exp_s, nonce, sig = parts
        try:
            exp = int(exp_s)
        except ValueError:
            return False
        if exp < time.time():
            return False
        payload = f"{exp}.{nonce}"
        expected = hmac.new(self.token.encode(), payload.encode(), hashlib.sha256).hexdigest()
        return secrets.compare_digest(sig, expected)

    def is_authenticated(self, request: Request) -> bool:
        if not self.auth_required:
            return True
        if self.misconfigured:
            return False
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            return self.verify_bearer(auth[7:])
        return self.verify_session_cookie(request.cookies.get(SESSION_COOKIE))

    def websocket_authenticated(self, ws: WebSocket) -> bool:
        if not self.auth_required:
            return True
        if self.misconfigured:
            return False
        auth = ws.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            return self.verify_bearer(auth[7:])
        return self.verify_session_cookie(ws.cookies.get(SESSION_COOKIE))

    def set_session_cookie(self, response: Response) -> None:
        response.set_cookie(
            SESSION_COOKIE,
            self.issue_session_value(),
            max_age=SESSION_MAX_AGE_S,
            httponly=True,
            secure=self.secure_cookies,
            samesite="lax",
            path="/",
        )

    def clear_session_cookie(self, response: Response) -> None:
        response.delete_cookie(SESSION_COOKIE, path="/")

    def login_with_token(self, token: str, request: Request) -> None:
        if self.misconfigured:
            raise HTTPException(status_code=503, detail=SETUP_MESSAGE)
        if not self._limiter.allowed(request):
            raise HTTPException(
                status_code=429,
                detail=f"Too many failed logins; try again in {int(self._limiter.remaining_lockout(request))}s",
            )
        if not self.verify_bearer(token):
            self._limiter.record_failure(request)
            raise HTTPException(status_code=401, detail="Invalid token")
        self._limiter.record_success(request)

    def status_payload(self) -> dict[str, Any]:
        return {
            "auth_required": self.auth_required,
            "configured": self.configured,
            "message": SETUP_MESSAGE if self.misconfigured else "",
        }


class WebAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: Any, auth: WebAuth) -> None:
        super().__init__(app)
        self.auth = auth

    async def dispatch(self, request: Request, call_next):
        if not self.auth.auth_required:
            return await call_next(request)
        path = request.url.path
        if self.auth.is_public_path(path, request.method):
            return await call_next(request)
        if self.auth.misconfigured:
            if path.startswith("/api/") or "application/json" in request.headers.get("accept", ""):
                return JSONResponse({"detail": SETUP_MESSAGE}, status_code=503)
            return RedirectResponse(url="/login", status_code=302)
        if self.auth.is_authenticated(request):
            return await call_next(request)
        if path.startswith("/api/") or "application/json" in request.headers.get("accept", ""):
            return JSONResponse({"detail": "Authentication required"}, status_code=401)
        return RedirectResponse(url="/login", status_code=302)


def register_auth_routes(app: FastAPI, auth: WebAuth, *, static_dir: Path) -> None:
    login_html = static_dir / "login.html"

    @app.get("/api/auth/status")
    def api_auth_status() -> dict[str, Any]:
        return auth.status_payload()

    @app.get("/login")
    def login_page(request: Request, token: str | None = None) -> Response:
        if not auth.auth_required:
            return RedirectResponse("/", status_code=302)
        if auth.misconfigured:
            html = login_html.read_text(encoding="utf-8") if login_html.is_file() else "<p>login.html missing</p>"
            banner = (
                f'<p id="setup-error" class="err">{SETUP_MESSAGE}</p>'
                '<p class="hint">On the server host, run <code>bash scripts/generate_web_token.sh</code> '
                "and restart the web service, then refresh this page.</p>"
            )
            if "<form" in html:
                html = html.replace("<form", banner + "<form", 1)
            else:
                html = banner + html
            return HTMLResponse(html, headers={"Cache-Control": "no-cache"})
        if token:
            try:
                auth.login_with_token(token, request)
            except HTTPException:
                return RedirectResponse("/login?error=1", status_code=302)
            resp = RedirectResponse("/", status_code=302)
            auth.set_session_cookie(resp)
            return resp
        if auth.is_authenticated(request):
            return RedirectResponse("/", status_code=302)
        if not login_html.is_file():
            raise HTTPException(status_code=500, detail="login.html missing")
        return HTMLResponse(login_html.read_text(encoding="utf-8"), headers={"Cache-Control": "no-cache"})

    @app.post("/api/auth/login")
    def api_login(body: LoginBody, request: Request) -> Response:
        if not auth.auth_required:
            return JSONResponse({"ok": True})
        auth.login_with_token(body.token, request)
        response = JSONResponse({"ok": True})
        auth.set_session_cookie(response)
        return response

    @app.post("/api/auth/logout")
    def api_logout() -> Response:
        response = JSONResponse({"ok": True})
        auth.clear_session_cookie(response)
        return response