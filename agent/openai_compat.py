"""OpenAI-compatible chat API client (llama-server, etc.) — no official SDK."""

from __future__ import annotations

import json
import logging
import socket
import urllib.error
import urllib.request
from ipaddress import ip_address
from typing import Any, Callable, Iterator
from urllib.parse import urlparse

from agent.integration_secrets import load_secret, redact
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_KEY_FILE = Path.home() / ".config" / "jahash-secrets" / "openai-api-key.txt"


class OpenAIChatError(RuntimeError):
  """Base for mapped HTTP / transport failures."""


class OpenAIConnectionError(OpenAIChatError):
  pass


class OpenAIAuthError(OpenAIChatError):
  pass


class OpenAIHTTPError(OpenAIChatError):
  def __init__(self, status: int, message: str) -> None:
    self.status = status
    super().__init__(message)


class OpenAIResponseError(OpenAIChatError):
  pass


def load_openai_api_key(
    *,
    env_var: str = "OPENAI_API_KEY",
    file_path: str | None = None,
) -> tuple[str, str, str | None]:
    path = None
    if file_path and str(file_path).strip():
        path = __import__("pathlib").Path(file_path).expanduser()
    else:
        import os
        from pathlib import Path

        raw = os.getenv("OPENAI_API_KEY_FILE", "").strip()
        path = Path(raw).expanduser() if raw else DEFAULT_KEY_FILE
    return load_secret(env_var=env_var, file_path=path, label="OpenAI API key")


def normalize_openai_base_url(raw: str) -> str:
    url = (raw or "").strip().rstrip("/")
    if not url:
        raise ValueError("OPENAI_BASE_URL is empty")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"OPENAI_BASE_URL must be http(s), got {parsed.scheme!r}")
    if not parsed.netloc:
        raise ValueError("OPENAI_BASE_URL must include a host")
    # Ensure /v1 suffix for OpenAI-style paths.
    if not url.endswith("/v1"):
        if url.endswith("/v1/"):
            url = url.rstrip("/")
        else:
            url = f"{url}/v1"
    return url


def _is_loopback_or_lan(host: str) -> bool:
    h = (host or "").lower().strip("[]")
    if h in ("localhost", "127.0.0.1", "::1"):
        return True
    try:
        addr = ip_address(h)
        return addr.is_loopback or addr.is_private or addr.is_link_local
    except ValueError:
        return False


def warn_openai_base_url(base_url: str) -> str | None:
    parsed = urlparse(base_url)
    host = parsed.hostname or ""
    if not _is_loopback_or_lan(host):
        return (
            f"OPENAI_BASE_URL host {host!r} is not loopback or private LAN — "
            "chat traffic leaves this machine."
        )
    return None


def _map_http_error(status: int, body: str, *, api_key: str) -> OpenAIChatError:
    detail = body.strip()[:400] if body else ""
    detail = redact(detail, api_key)
    if status in (401, 403):
        return OpenAIAuthError(f"OpenAI API authentication failed (HTTP {status}): {detail}")
    if status == 429:
        return OpenAIHTTPError(status, f"OpenAI API rate limited (HTTP 429): {detail}")
    if status >= 500:
        return OpenAIHTTPError(status, f"OpenAI API server error (HTTP {status}): {detail}")
    return OpenAIHTTPError(status, f"OpenAI API error (HTTP {status}): {detail}")


def _kwargs_to_openai(**kwargs: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    options = kwargs.get("options") or {}
    if not isinstance(options, dict):
        options = {}
    if "temperature" in kwargs:
        out["temperature"] = kwargs["temperature"]
    elif "temperature" in options:
        out["temperature"] = options["temperature"]
    if "max_tokens" in kwargs:
        out["max_tokens"] = kwargs["max_tokens"]
    elif "num_predict" in options:
        out["max_tokens"] = options["num_predict"]
    elif "max_tokens" in options:
        out["max_tokens"] = options["max_tokens"]
    if "tools" in kwargs and kwargs["tools"]:
        out["tools"] = kwargs["tools"]
        out["tool_choice"] = kwargs.get("tool_choice", "auto")
    return out


def _tool_calls_to_ollama(tool_calls: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    if not tool_calls:
        return []
    out: list[dict[str, Any]] = []
    for tc in tool_calls:
        fn = tc.get("function") or {}
        args = fn.get("arguments") or ""
        if isinstance(args, dict):
            args = json.dumps(args)
        out.append(
            {
                "function": {
                    "name": fn.get("name", ""),
                    "arguments": args,
                }
            }
        )
    return out


class OpenAIChat:
    """Chat completions against an OpenAI-compatible HTTP endpoint."""

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        api_key: str = "",
        timeout_s: float = 120.0,
        model_getter: Callable[[], str] | None = None,
    ) -> None:
        self._stored_model = model
        self._model_getter = model_getter
        self.base_url = normalize_openai_base_url(base_url)
        self.api_key = api_key
        self.timeout_s = timeout_s
        warn = warn_openai_base_url(self.base_url)
        if warn:
            logger.warning(warn)

    @property
    def model(self) -> str:
        return self._model_getter() if self._model_getter else self._stored_model

    @model.setter
    def model(self, value: str) -> None:
        self._stored_model = value
        self._model_getter = None

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None,
        *,
        stream: bool = False,
    ) -> urllib.request.addinfourl | bytes:
        url = f"{self.base_url.rstrip('/')}{path}"
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers=self._headers())
        if stream:
            req.add_header("Accept", "text/event-stream")
        try:
            return urllib.request.urlopen(req, timeout=self.timeout_s)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode(errors="replace") if exc.fp else ""
            raise _map_http_error(exc.code, body, api_key=self.api_key) from exc
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            raise OpenAIConnectionError(f"Could not reach OpenAI API at {url}: {reason}") from exc
        except socket.timeout as exc:
            raise OpenAIConnectionError(f"OpenAI API timed out after {self.timeout_s}s") from exc

    def list_models(self) -> list[dict[str, Any]]:
        with self._request("GET", "/models", None) as resp:
            raw = resp.read().decode() or "{}"
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise OpenAIResponseError("Invalid JSON from /models") from exc
        rows = data.get("data") or data.get("models") or []
        out: list[dict[str, Any]] = []
        for row in rows:
            if isinstance(row, str):
                out.append({"id": row, "name": row})
            elif isinstance(row, dict):
                mid = row.get("id") or row.get("name") or ""
                if mid:
                    out.append({"id": mid, "name": mid, **row})
        return out

    def chat_completion(self, messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        """Return Ollama-shaped ``{"message": {"content", "tool_calls"?}}``."""
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            **_kwargs_to_openai(**kwargs),
        }
        with self._request("POST", "/chat/completions", payload) as resp:
            raw = resp.read().decode() or "{}"
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise OpenAIResponseError("Invalid JSON from chat/completions") from exc
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        content = (msg.get("content") or "").strip()
        tool_calls = _tool_calls_to_ollama(msg.get("tool_calls"))
        message: dict[str, Any] = {"role": msg.get("role", "assistant"), "content": content}
        if tool_calls:
            message["tool_calls"] = tool_calls
        return {"message": message, "raw": data}

    def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> dict[str, Any]:
        shaped = self.chat_completion(messages, **kwargs)
        return {"content": (shaped.get("message") or {}).get("content", "") or ""}

    def chat_stream(self, messages: list[dict[str, str]], **kwargs: Any) -> Iterator[str]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            **_kwargs_to_openai(**kwargs),
        }
        resp = self._request("POST", "/chat/completions", payload, stream=True)
        try:
            while True:
                raw = resp.readline()
                if not raw:
                    break
                line = raw.decode(errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                data_s = line[5:].strip()
                if data_s == "[DONE]":
                    break
                try:
                    chunk = json.loads(data_s)
                except json.JSONDecodeError:
                    continue
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                token = delta.get("content") or ""
                if token:
                    yield token
        finally:
            if hasattr(resp, "close"):
                resp.close()