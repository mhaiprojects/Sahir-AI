"""Redact sensitive query-string values from log lines before they are written."""

from __future__ import annotations

import logging
import re
from typing import Any

_SENSITIVE_QUERY_PARAMS = ("token", "access_token", "key", "password")
_QUERY_SECRET_RE = re.compile(
    r"([?&](?:" + "|".join(_SENSITIVE_QUERY_PARAMS) + r")=)([^&\s\"'<>]+)",
    re.IGNORECASE,
)


def redact_query_secrets(text: str) -> str:
    if not text or "token=" not in text.lower() and "password=" not in text.lower():
        return text
    return _QUERY_SECRET_RE.sub(r"\1***", text)


class RedactQuerySecretsFilter(logging.Filter):
    """Logging filter that redacts sensitive query values in the formatted message."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_query_secrets(record.msg)
        if record.args:
            record.args = tuple(
                redact_query_secrets(a) if isinstance(a, str) else a for a in record.args
            )
        return True


def install_log_redaction() -> RedactQuerySecretsFilter:
    """Attach redaction filter to root and uvicorn access/error loggers."""
    filt = RedactQuerySecretsFilter()
    root = logging.getLogger()
    if not any(isinstance(f, RedactQuerySecretsFilter) for f in root.filters):
        root.addFilter(filt)
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        log = logging.getLogger(name)
        if not any(isinstance(f, RedactQuerySecretsFilter) for f in log.filters):
            log.addFilter(filt)
        for handler in log.handlers:
            if not any(isinstance(f, RedactQuerySecretsFilter) for f in handler.filters):
                handler.addFilter(filt)
    for handler in root.handlers:
        if not any(isinstance(f, RedactQuerySecretsFilter) for f in handler.filters):
            handler.addFilter(filt)
    return filt


def count_unredacted_token_params(text: str) -> int:
    """Count query token= (etc.) values that are not already '***'."""
    n = 0
    for m in _QUERY_SECRET_RE.finditer(text):
        if m.group(2) != "***":
            n += 1
    return n