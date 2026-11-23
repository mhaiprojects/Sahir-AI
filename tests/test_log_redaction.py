"""Query-string secret redaction for access logs."""

from __future__ import annotations

import logging

from log_redaction import RedactQuerySecretsFilter, count_unredacted_token_params, redact_query_secrets


def test_redact_login_token_in_access_line() -> None:
    line = '127.0.0.1:99 - "GET /login?token=supersecretvalue HTTP/1.1" 302'
    out = redact_query_secrets(line)
    assert "supersecretvalue" not in out
    assert "token=***" in out
    assert count_unredacted_token_params(out) == 0


def test_redact_multiple_query_params() -> None:
    raw = "/path?token=a&password=b&access_token=c&key=d&keep=visible"
    out = redact_query_secrets(raw)
    assert "token=***" in out
    assert "password=***" in out
    assert "keep=visible" in out


def test_logging_filter_on_record() -> None:
    filt = RedactQuerySecretsFilter()
    rec = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/1.1" %d',
        ("client", "GET", "/login?token=hiddenvalue", 302),
        None,
    )
    filt.filter(rec)
    assert rec.args is not None
    assert "hiddenvalue" not in str(rec.args)