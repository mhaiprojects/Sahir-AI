"""Compute and verify HTTPS certificate Subject Alternative Names for Sahir."""

from __future__ import annotations

import argparse
import json
import re
import socket
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

MAX_DAYS_IOS = 825
_SKIP_IFACE_RE = re.compile(r"^(docker\d*|br-|veth)")


@dataclass(frozen=True)
class SanEntry:
    kind: str  # DNS or IP
    value: str

    def openssl(self) -> str:
        return f"{self.kind}:{self.value}"

    def __hash__(self) -> int:
        return hash((self.kind.upper(), self._norm()))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SanEntry):
            return False
        return self.kind.upper() == other.kind.upper() and self._norm() == other._norm()

    def _norm(self) -> str:
        if self.kind.upper() == "IP":
            return _norm_ip(self.value)
        if self.kind.upper() == "DNS":
            return self.value.rstrip(".").lower()
        return self.value


def _norm_ip(ip: str) -> str:
    try:
        import ipaddress

        return str(ipaddress.ip_address(ip))
    except ValueError:
        return ip


def _hostname_names() -> list[SanEntry]:
    host = socket.gethostname().strip()
    out: list[SanEntry] = []
    if host:
        out.append(SanEntry("DNS", host))
        out.append(SanEntry("DNS", f"{host}.local"))
    return out


def _interface_addresses() -> list[SanEntry]:
    """Non-loopback addresses on UP interfaces (skip docker bridges)."""
    try:
        proc = subprocess.run(
            ["ip", "-j", "addr", "show", "up"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        data = json.loads(proc.stdout)
    except (subprocess.SubprocessError, json.JSONDecodeError, FileNotFoundError):
        return []

    out: list[SanEntry] = []
    import ipaddress

    for iface in data:
        name = str(iface.get("ifname", ""))
        if _SKIP_IFACE_RE.match(name):
            continue
        for addr in iface.get("addr_info") or []:
            if addr.get("scope") == "host":
                continue
            family = addr.get("family")
            local = str(addr.get("local", "")).split("%")[0]
            if not local:
                continue
            if family == "inet":
                if local.startswith("127."):
                    continue
                out.append(SanEntry("IP", local))
            elif family == "inet6":
                if local == "::1":
                    continue
                try:
                    ip = ipaddress.ip_address(local)
                except ValueError:
                    continue
                if ip.is_link_local:
                    continue
                out.append(SanEntry("IP", local))
    return out


def _tailscale_sans() -> list[SanEntry]:
    out: list[SanEntry] = []
    for cmd in (["tailscale", "ip", "-4"], ["tailscale", "ip", "-6"]):
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        except (subprocess.SubprocessError, FileNotFoundError):
            continue
        if proc.returncode != 0:
            continue
        for line in proc.stdout.splitlines():
            ip = line.strip()
            if ip:
                out.append(SanEntry("IP", ip))
    try:
        proc = subprocess.run(
            ["tailscale", "status", "--json"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if proc.returncode == 0:
            data = json.loads(proc.stdout)
            dns = str((data.get("Self") or {}).get("DNSName") or "").strip()
            if dns:
                out.append(SanEntry("DNS", dns.rstrip(".")))
    except (subprocess.SubprocessError, json.JSONDecodeError, FileNotFoundError):
        pass
    return out


def parse_extra_sans(raw: str) -> list[SanEntry]:
    out: list[SanEntry] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" in part and part.split(":", 1)[0].upper() in ("DNS", "IP"):
            kind, val = part.split(":", 1)
            out.append(SanEntry(kind.upper(), val.strip()))
        else:
            out.append(SanEntry("DNS", part))
    return out


def collect_desired_sans(
    *,
    cn: str,
    extra: Iterable[str] | None = None,
    include_legacy: bool = True,
) -> set[SanEntry]:
    """SAN entries written into a newly generated certificate."""
    entries: list[SanEntry] = [
        SanEntry("DNS", "localhost"),
        SanEntry("IP", "127.0.0.1"),
        SanEntry("IP", "::1"),
        SanEntry("DNS", cn),
    ]
    if include_legacy:
        entries.append(SanEntry("DNS", "voice-assistant"))
    entries.extend(_hostname_names())
    entries.extend(_interface_addresses())
    entries.extend(_tailscale_sans())
    for item in extra or ():
        entries.extend(parse_extra_sans(item))
    return set(entries)


def collect_required_check_sans(
    *,
    cn: str,
    extra: Iterable[str] | None = None,
) -> set[SanEntry]:
    """SANs that must be present for --check; omits legacy DNS and optional IPv6."""
    entries: list[SanEntry] = [
        SanEntry("DNS", "localhost"),
        SanEntry("IP", "127.0.0.1"),
        SanEntry("DNS", cn),
    ]
    entries.extend(_hostname_names())
    entries.extend(
        e for e in _interface_addresses() if e.kind.upper() == "IP" and ":" not in e.value
    )
    for e in _tailscale_sans():
        if e.kind.upper() == "DNS":
            entries.append(e)
        elif e.kind.upper() == "IP" and ":" not in e.value:
            entries.append(e)
    for item in extra or ():
        entries.extend(parse_extra_sans(item))
    return set(entries)


def format_openssl_san(sans: set[SanEntry]) -> str:
    dns = sorted({s.value for s in sans if s.kind.upper() == "DNS"}, key=str.lower)
    ips = sorted({s.value for s in sans if s.kind.upper() == "IP"}, key=lambda x: (":" in x, x))
    parts = [f"DNS:{d}" for d in dns] + [f"IP:{ip}" for ip in ips]
    return ",".join(parts)


_DNS_SAN_RE = re.compile(r"^DNS:\s*(.+)$", re.I)
_IP_SAN_RE = re.compile(r"^IP(?:\s+Address)?\s*:\s*(.+)$", re.I)


def parse_san_extension_text(text: str) -> set[SanEntry]:
    """Parse openssl ``-ext subjectAltName`` output (any IP Address: casing)."""
    m = re.search(r"X509v3 Subject Alternative Name:\s*\n?\s*(.+)", text, re.DOTALL)
    if not m:
        return set()
    blob = m.group(1).replace("\n", " ").strip()
    out: set[SanEntry] = set()
    for part in blob.split(","):
        part = part.strip()
        if not part:
            continue
        dm = _DNS_SAN_RE.match(part)
        if dm:
            out.add(SanEntry("DNS", dm.group(1).strip()))
            continue
        im = _IP_SAN_RE.match(part)
        if im:
            out.add(SanEntry("IP", im.group(1).strip()))
    return out


def parse_cert_sans(cert_path: Path) -> set[SanEntry]:
    proc = subprocess.run(
        ["openssl", "x509", "-in", str(cert_path), "-noout", "-ext", "subjectAltName"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return set()
    return parse_san_extension_text(proc.stdout + proc.stderr)


def missing_from_cert(cert_sans: set[SanEntry], desired: set[SanEntry]) -> set[SanEntry]:
    have = {SanEntry(s.kind, s._norm()) for s in cert_sans}

    def covered(entry: SanEntry) -> bool:
        key = SanEntry(entry.kind, entry._norm())
        return key in have

    return {d for d in desired if not covered(d)}


def cert_not_after(cert_path: Path) -> datetime | None:
    proc = subprocess.run(
        ["openssl", "x509", "-in", str(cert_path), "-noout", "-enddate"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    m = re.search(r"notAfter=(.+)", proc.stdout)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1).strip(), "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def check_certificate(cert_path: Path, *, cn: str, extra: Iterable[str] = ()) -> tuple[bool, list[str]]:
    """Return (ok, human messages). Stale when desired SANs are missing from cert."""
    if not cert_path.is_file():
        return False, [f"Certificate not found: {cert_path}"]
    desired = collect_required_check_sans(cn=cn, extra=extra)
    cert_sans = parse_cert_sans(cert_path)
    missing = missing_from_cert(cert_sans, desired)
    lines: list[str] = []
    if missing:
        miss = ", ".join(sorted(s.openssl() for s in missing))
        lines.append(f"Certificate is missing current SANs: {miss}")
        lines.append("Regenerate: FORCE=1 bash scripts/generate_web_cert.sh")
    expiry = cert_not_after(cert_path)
    if expiry and expiry < datetime.now(timezone.utc):
        lines.append(f"Certificate expired on {expiry.isoformat()}")
    return (not lines, lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Sahir web TLS SAN helpers")
    p.add_argument("--check", action="store_true", help="Exit 1 if cert SANs are stale")
    p.add_argument("--print-sans", action="store_true", help="Print desired SAN list for this host")
    p.add_argument("--cert", type=Path, default=None, help="Certificate path (default: data/certs/web.crt)")
    p.add_argument("--cn", default="", help="Certificate CN / primary DNS name")
    p.add_argument("--extra", default="", help="Comma-separated extra SANs (DNS: or IP: or bare DNS)")
    args = p.parse_args(argv)
    root = Path(__file__).resolve().parent.parent
    cn = args.cn or __import__("os").environ.get("WEB_CERT_CN", "voice-assistant.local")
    extra = [args.extra] if args.extra else []
    if __import__("os").environ.get("EXTRA_SANS", "").strip():
        extra.append(__import__("os").environ["EXTRA_SANS"].strip())

    if args.print_sans:
        sans = collect_desired_sans(cn=cn, extra=extra)
        print(format_openssl_san(sans))
        return 0

    cert = args.cert or (root / "data" / "certs" / "web.crt")
    ok, messages = check_certificate(cert, cn=cn, extra=extra)
    for line in messages:
        print(line, file=sys.stderr if not ok else sys.stdout)
    if ok:
        expiry = cert_not_after(cert)
        if expiry:
            print(f"Certificate SANs OK; expires {expiry.strftime('%Y-%m-%d %H:%M UTC')}")
        else:
            print("Certificate SANs OK")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())