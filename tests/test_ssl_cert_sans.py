"""TLS SAN computation and certificate --check parsing."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from web.ssl_cert_check import (
    SanEntry,
    check_certificate,
    collect_desired_sans,
    collect_required_check_sans,
    format_openssl_san,
    missing_from_cert,
    parse_cert_sans,
    parse_extra_sans,
    parse_san_extension_text,
)


def test_parse_extra_sans() -> None:
    got = parse_extra_sans("DNS:phone.local,IP:192.168.1.50,extra")
    assert SanEntry("DNS", "phone.local") in got
    assert SanEntry("IP", "192.168.1.50") in got
    assert SanEntry("DNS", "extra") in got


def test_collect_desired_sans_with_mocked_interfaces() -> None:
    fake_ip = [
        {
            "ifname": "wlp5s0",
            "addr_info": [{"family": "inet", "local": "192.168.1.220", "scope": "global"}],
        }
    ]
    with patch("web.ssl_cert_check._interface_addresses", return_value=[SanEntry("IP", "192.168.1.220")]):
        with patch("web.ssl_cert_check._tailscale_sans", return_value=[SanEntry("IP", "100.65.107.40")]):
            with patch("web.ssl_cert_check._hostname_names", return_value=[SanEntry("DNS", "buspc")]):
                sans = collect_desired_sans(cn="voice-assistant.local")
    assert SanEntry("IP", "192.168.1.220") in sans
    assert SanEntry("IP", "100.65.107.40") in sans
    assert SanEntry("DNS", "localhost") in sans
    assert SanEntry("IP", "127.0.0.1") in sans


def test_format_openssl_san_stable() -> None:
    s = {SanEntry("DNS", "localhost"), SanEntry("IP", "127.0.0.1")}
    assert format_openssl_san(s) == "DNS:localhost,IP:127.0.0.1"


BUSPC_SAN_EXT = (
    "X509v3 Subject Alternative Name: \n"
    "    DNS:buspc, DNS:buspc.local, DNS:buspc.tail0e2353.ts.net, DNS:localhost, "
    "DNS:voice-assistant.local, IP Address:127.0.0.1, IP Address:192.168.1.220, "
    "IP Address:100.65.107.40\n"
)


def test_parse_san_extension_ip_address_casing() -> None:
    got = parse_san_extension_text(BUSPC_SAN_EXT)
    assert SanEntry("IP", "127.0.0.1") in got
    assert SanEntry("IP", "192.168.1.220") in got
    assert SanEntry("IP", "100.65.107.40") in got
    assert SanEntry("DNS", "buspc.tail0e2353.ts.net") in got


def test_parse_cert_sans_openssl_output() -> None:
    with patch("subprocess.run") as run:
        run.return_value.returncode = 0
        run.return_value.stdout = BUSPC_SAN_EXT
        run.return_value.stderr = ""
        got = parse_cert_sans(Path("/tmp/x.crt"))
    assert SanEntry("DNS", "localhost") in got
    assert SanEntry("IP", "192.168.1.220") in got


def test_check_not_stale_for_buspc_san_blob(tmp_path: Path) -> None:
    cert = tmp_path / "web.crt"
    cert.write_bytes(b"dummy")
    hostname = [SanEntry("DNS", "buspc"), SanEntry("DNS", "buspc.local")]
    iface = [SanEntry("IP", "192.168.1.220")]
    ts = [
        SanEntry("IP", "100.65.107.40"),
        SanEntry("DNS", "buspc.tail0e2353.ts.net"),
        SanEntry("IP", "fd7a:115c:a1e0::dc01:6bfe"),
    ]

    def fake_run(cmd, *args, **kwargs):
        from types import SimpleNamespace

        if "-ext" in cmd and "subjectAltName" in cmd:
            return SimpleNamespace(returncode=0, stdout=BUSPC_SAN_EXT, stderr="")
        if "-enddate" in cmd:
            return SimpleNamespace(
                returncode=0, stdout="notAfter=Dec 31 23:59:59 2027 GMT\n", stderr=""
            )
        raise AssertionError(cmd)

    with patch("web.ssl_cert_check.subprocess.run", side_effect=fake_run):
        with patch("web.ssl_cert_check._hostname_names", return_value=hostname):
            with patch("web.ssl_cert_check._interface_addresses", return_value=iface):
                with patch("web.ssl_cert_check._tailscale_sans", return_value=ts):
                    ok, msgs = check_certificate(
                        cert, cn="voice-assistant.local"
                    )
    assert ok, msgs


def test_required_check_sans_omit_optional_ipv6_and_legacy() -> None:
    with patch("web.ssl_cert_check._hostname_names", return_value=[SanEntry("DNS", "buspc")]):
        with patch(
            "web.ssl_cert_check._interface_addresses",
            return_value=[
                SanEntry("IP", "192.168.1.220"),
                SanEntry("IP", "fd7a:115c:a1e0::dc01:6bfe"),
            ],
        ):
            with patch(
                "web.ssl_cert_check._tailscale_sans",
                return_value=[
                    SanEntry("IP", "100.65.107.40"),
                    SanEntry("IP", "fd7a:115c:a1e0::dc01:6bfe"),
                ],
            ):
                req = collect_required_check_sans(cn="voice-assistant.local")
    assert SanEntry("DNS", "voice-assistant") not in req
    assert SanEntry("IP", "::1") not in req
    assert SanEntry("IP", "fd7a:115c:a1e0::dc01:6bfe") not in req
    assert SanEntry("IP", "192.168.1.220") in req
    assert SanEntry("IP", "100.65.107.40") in req


def test_missing_from_cert() -> None:
    cert = {SanEntry("DNS", "localhost"), SanEntry("IP", "127.0.0.1")}
    want = {SanEntry("IP", "192.168.1.220"), SanEntry("DNS", "localhost")}
    assert missing_from_cert(cert, want) == {SanEntry("IP", "192.168.1.220")}


def test_check_certificate_missing_file(tmp_path: Path) -> None:
    ok, msgs = check_certificate(tmp_path / "nope.crt", cn="voice-assistant.local")
    assert not ok
    assert "not found" in msgs[0].lower()


def test_main_check_exit_code(tmp_path: Path) -> None:
    from web.ssl_cert_check import main

    missing = tmp_path / "missing.crt"
    code = main(["--check", "--cert", str(missing), "--cn", "voice-assistant.local"])
    assert code == 1