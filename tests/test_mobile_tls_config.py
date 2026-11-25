from __future__ import annotations

import ipaddress
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from mobile_api.tls_config import TLSCertificateError, validate_tls_certificate


def _write_certificate(
    folder: Path,
    *,
    now: datetime,
    dns_names: tuple[str, ...] = ("assistant.example.com",),
    ip_addresses: tuple[str, ...] = (),
    not_before: datetime | None = None,
    not_after: datetime | None = None,
):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "VoiceAssistant Test")])
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before((not_before or now - timedelta(days=1)).replace(tzinfo=None))
        .not_valid_after((not_after or now + timedelta(days=90)).replace(tzinfo=None))
    )
    san_items = [x509.DNSName(name) for name in dns_names]
    san_items.extend(x509.IPAddress(ipaddress.ip_address(value)) for value in ip_addresses)
    if san_items:
        builder = builder.add_extension(x509.SubjectAlternativeName(san_items), critical=False)
    certificate = builder.sign(private_key=key, algorithm=hashes.SHA256())

    cert_path = folder / "server.crt"
    key_path = folder / "server.key"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    return cert_path, key_path, key


def test_valid_tls_certificate_and_public_host_are_accepted(tmp_path):
    now = datetime.now(timezone.utc)
    cert, key, _ = _write_certificate(tmp_path, now=now)

    result = validate_tls_certificate(
        cert,
        key,
        public_url="https://assistant.example.com",
        now=now,
    )

    assert result["hostname"] == "assistant.example.com"
    assert result["days_remaining"] > 89
    assert result["renewal_warning"] is False


def test_certificate_expiry_warning_is_reported(tmp_path):
    now = datetime.now(timezone.utc)
    cert, key, _ = _write_certificate(
        tmp_path,
        now=now,
        not_after=now + timedelta(days=5),
    )

    result = validate_tls_certificate(cert, key, now=now)
    assert result["renewal_warning"] is True


def test_expired_certificate_is_rejected(tmp_path):
    now = datetime.now(timezone.utc)
    cert, key, _ = _write_certificate(
        tmp_path,
        now=now,
        not_after=now - timedelta(seconds=1),
    )

    with pytest.raises(TLSCertificateError, match="expired"):
        validate_tls_certificate(cert, key, now=now)


def test_not_yet_valid_certificate_is_rejected(tmp_path):
    now = datetime.now(timezone.utc)
    cert, key, _ = _write_certificate(
        tmp_path,
        now=now,
        not_before=now + timedelta(days=1),
    )

    with pytest.raises(TLSCertificateError, match="not valid yet"):
        validate_tls_certificate(cert, key, now=now)


def test_public_url_host_must_match_certificate_san(tmp_path):
    now = datetime.now(timezone.utc)
    cert, key, _ = _write_certificate(tmp_path, now=now)

    with pytest.raises(TLSCertificateError, match="subjectAltName"):
        validate_tls_certificate(
            cert,
            key,
            public_url="https://other.example.com",
            now=now,
        )


def test_wildcard_san_matches_one_subdomain_level(tmp_path):
    now = datetime.now(timezone.utc)
    cert, key, _ = _write_certificate(tmp_path, now=now, dns_names=("*.example.com",))

    validate_tls_certificate(cert, key, public_url="https://phone.example.com", now=now)
    with pytest.raises(TLSCertificateError, match="subjectAltName"):
        validate_tls_certificate(cert, key, public_url="https://a.phone.example.com", now=now)


def test_ip_public_url_requires_matching_ip_san(tmp_path):
    now = datetime.now(timezone.utc)
    cert, key, _ = _write_certificate(
        tmp_path,
        now=now,
        dns_names=(),
        ip_addresses=("100.64.0.10",),
    )

    validate_tls_certificate(cert, key, public_url="https://100.64.0.10:8765", now=now)
    with pytest.raises(TLSCertificateError, match="subjectAltName"):
        validate_tls_certificate(cert, key, public_url="https://100.64.0.11", now=now)


def test_certificate_and_private_key_must_match(tmp_path):
    now = datetime.now(timezone.utc)
    cert, key, _ = _write_certificate(tmp_path, now=now)
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    key.write_bytes(
        other_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )

    with pytest.raises(TLSCertificateError, match="matching pair"):
        validate_tls_certificate(cert, key, now=now)


def test_public_url_must_be_a_clean_https_origin(tmp_path):
    now = datetime.now(timezone.utc)
    cert, key, _ = _write_certificate(tmp_path, now=now)

    with pytest.raises(TLSCertificateError, match="clean HTTPS origin"):
        validate_tls_certificate(
            cert,
            key,
            public_url="http://assistant.example.com/path",
            now=now,
        )


def test_missing_certificate_or_key_is_rejected(tmp_path):
    with pytest.raises(TLSCertificateError, match="does not exist"):
        validate_tls_certificate(tmp_path / "missing.crt", tmp_path / "missing.key")


def test_naive_validation_clock_is_rejected(tmp_path):
    now = datetime.now(timezone.utc)
    cert, key, _ = _write_certificate(tmp_path, now=now)

    with pytest.raises(TLSCertificateError, match="timezone-aware"):
        validate_tls_certificate(cert, key, now=datetime.now())


def test_invalid_warning_window_is_rejected(tmp_path):
    now = datetime.now(timezone.utc)
    cert, key, _ = _write_certificate(tmp_path, now=now)

    with pytest.raises(TLSCertificateError, match="warning_days"):
        validate_tls_certificate(cert, key, now=now, warning_days=0)



def test_trusted_proxy_ip_configuration_accepts_explicit_addresses_and_networks(monkeypatch):
    from mobile_api.server import _trusted_proxy_ips_from_env

    monkeypatch.setenv("VOICEASSISTANT_TRUSTED_PROXY_IPS", "127.0.0.1,10.20.0.0/24")
    assert _trusted_proxy_ips_from_env() == "127.0.0.1,10.20.0.0/24"


@pytest.mark.parametrize("value", ["*", "0.0.0.0/0", "not-an-ip", ""])
def test_trusted_proxy_ip_configuration_rejects_wildcard_or_invalid_values(monkeypatch, value):
    from mobile_api.server import _trusted_proxy_ips_from_env

    monkeypatch.setenv("VOICEASSISTANT_TRUSTED_PROXY_IPS", value)
    with pytest.raises(RuntimeError, match="TRUSTED_PROXY_IPS"):
        _trusted_proxy_ips_from_env()


def test_trusted_proxy_ip_configuration_defaults_to_loopback_only(monkeypatch):
    from mobile_api.server import _trusted_proxy_ips_from_env

    monkeypatch.delenv("VOICEASSISTANT_TRUSTED_PROXY_IPS", raising=False)
    assert _trusted_proxy_ips_from_env() == "127.0.0.1,::1"


def test_mobile_server_tls_options_accept_a_valid_certificate_pair(tmp_path, monkeypatch):
    from mobile_api.server import _tls_options_from_env

    now = datetime.now(timezone.utc)
    cert, key, _ = _write_certificate(tmp_path, now=now)
    monkeypatch.setenv("VOICEASSISTANT_TLS_CERTFILE", str(cert))
    monkeypatch.setenv("VOICEASSISTANT_TLS_KEYFILE", str(key))
    monkeypatch.delenv("VOICEASSISTANT_MOBILE_PUBLIC_URL", raising=False)

    assert _tls_options_from_env() == {
        "ssl_certfile": str(cert),
        "ssl_keyfile": str(key),
    }
