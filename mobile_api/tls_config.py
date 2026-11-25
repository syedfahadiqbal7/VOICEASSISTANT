"""Preflight validation for the desktop mobile API TLS certificate."""
from __future__ import annotations

import ipaddress
import ssl
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from cryptography import x509


class TLSCertificateError(RuntimeError):
    """Configured TLS certificate cannot safely serve the mobile API."""


def _dns_name_matches(host: str, pattern: str) -> bool:
    host = host.encode("idna").decode("ascii").lower().rstrip(".")
    pattern = pattern.encode("idna").decode("ascii").lower().rstrip(".")
    if pattern.startswith("*."):
        suffix = pattern[1:]
        return host.endswith(suffix) and host.count(".") == pattern.count(".")
    return host == pattern


def _certificate_matches_host(certificate: x509.Certificate, host: str) -> bool:
    try:
        san = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except x509.ExtensionNotFound:
        return False

    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return any(_dns_name_matches(host, name) for name in san.get_values_for_type(x509.DNSName))
    return address in san.get_values_for_type(x509.IPAddress)


def validate_tls_certificate(
    certfile: str | Path,
    keyfile: str | Path,
    *,
    public_url: str | None = None,
    now: datetime | None = None,
    warning_days: int = 30,
) -> dict[str, object]:
    """Validate certificate/key pairing, validity window and optional public host SAN.

    The private key is loaded by OpenSSL but never returned or logged. Uvicorn
    loads the configured files again when its TLS listener is created.
    """
    cert_path = Path(certfile)
    key_path = Path(keyfile)
    if not cert_path.is_file() or not key_path.is_file():
        raise TLSCertificateError("Configured TLS certificate or private key file does not exist")
    if cert_path.stat().st_size > 1024 * 1024 or key_path.stat().st_size > 1024 * 1024:
        raise TLSCertificateError("TLS certificate and private key files must each be 1 MiB or smaller")

    try:
        certificate = x509.load_pem_x509_certificate(cert_path.read_bytes())
    except Exception as error:
        raise TLSCertificateError("TLS certificate is not a valid PEM X.509 certificate") from error

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise TLSCertificateError("TLS validation clock must be timezone-aware")
    current = current.astimezone(timezone.utc)
    valid_from = certificate.not_valid_before.replace(tzinfo=timezone.utc)
    valid_until = certificate.not_valid_after.replace(tzinfo=timezone.utc)
    if current < valid_from:
        raise TLSCertificateError("TLS certificate is not valid yet")
    if current >= valid_until:
        raise TLSCertificateError("TLS certificate has expired")
    if isinstance(warning_days, bool) or not isinstance(warning_days, int) or not 1 <= warning_days <= 365:
        raise TLSCertificateError("TLS warning_days must be between 1 and 365")

    try:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
    except (OSError, ssl.SSLError, ValueError) as error:
        raise TLSCertificateError("TLS certificate and private key could not be loaded as a matching pair") from error

    hostname = None
    if public_url:
        try:
            parsed = urlsplit(public_url.strip())
            _ = parsed.port
        except ValueError as error:
            raise TLSCertificateError("VOICEASSISTANT_MOBILE_PUBLIC_URL is invalid") from error
        if (
            parsed.scheme.lower() != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in ("", "/")
        ):
            raise TLSCertificateError("Direct TLS public URL must be a clean HTTPS origin")
        hostname = parsed.hostname
        if not _certificate_matches_host(certificate, hostname):
            raise TLSCertificateError("TLS certificate subjectAltName does not match the configured public URL host")

    days_remaining = (valid_until - current).total_seconds() / 86400
    return {
        "hostname": hostname,
        "valid_from": valid_from.isoformat(),
        "valid_until": valid_until.isoformat(),
        "days_remaining": days_remaining,
        "renewal_warning": days_remaining <= warning_days,
    }
