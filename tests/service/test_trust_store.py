"""The custom CA trust store adds an operator's CA certificates to the public roots.

Each case builds its certificates in ``tmp_path`` and passes its own environment
mapping, so nothing here changes the test process's environment.
"""

from __future__ import annotations

import datetime
import stat
from pathlib import Path
from typing import TYPE_CHECKING

import certifi
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from structlog.testing import capture_logs

from infrahub_sync.service.trust_store import (
    BUNDLE_NAME,
    TRUST_VARIABLES,
    CustomCertificateError,
    install_custom_ca_bundle,
)

if TYPE_CHECKING:
    from collections.abc import Callable

DIRECTORY_SETTING = "INFRAHUB_SYNC_CA_DIRECTORY"


def _private_key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


def _ca_certificate(common_name: str) -> bytes:
    """Return one self-signed CA certificate in PEM form."""
    key = _private_key()
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = datetime.datetime.now(datetime.timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    return certificate.public_bytes(serialization.Encoding.PEM)


def _pkcs8_key() -> bytes:
    return _private_key().private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )


def _traditional_key() -> bytes:
    """Return a key whose PEM header is ``BEGIN RSA PRIVATE KEY``, without generating RSA."""
    body = _pkcs8_key().replace(b"BEGIN PRIVATE KEY", b"BEGIN RSA PRIVATE KEY")
    return body.replace(b"END PRIVATE KEY", b"END RSA PRIVATE KEY")


def _environment(tmp_path: Path) -> dict[str, str]:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    return {"TMPDIR": str(scratch)}


def test_an_absent_directory_changes_nothing(tmp_path: Path) -> None:
    environ = _environment(tmp_path)
    before = dict(environ)

    assert install_custom_ca_bundle(tmp_path / "missing", environ) is None

    assert environ == before
    assert not (tmp_path / "scratch" / BUNDLE_NAME).exists()


def test_an_unset_directory_setting_changes_nothing(tmp_path: Path) -> None:
    environ = _environment(tmp_path)
    before = dict(environ)

    assert install_custom_ca_bundle(None, environ) is None

    assert environ == before
    assert not (tmp_path / "scratch" / BUNDLE_NAME).exists()


def test_an_empty_directory_changes_nothing(tmp_path: Path) -> None:
    directory = tmp_path / "ca"
    directory.mkdir()
    (directory / "README.txt").write_text("not a certificate", encoding="utf-8")
    environ = _environment(tmp_path)
    environ[DIRECTORY_SETTING] = str(directory)
    before = dict(environ)

    assert install_custom_ca_bundle(None, environ) is None

    assert environ == before
    assert not (tmp_path / "scratch" / BUNDLE_NAME).exists()


def test_the_bundle_holds_the_public_roots_then_the_operator_files_in_name_order(tmp_path: Path) -> None:
    directory = tmp_path / "ca"
    directory.mkdir()
    second = _ca_certificate("second")
    first = _ca_certificate("first")
    (directory / "b-second.pem").write_bytes(second)
    (directory / "a-first.crt").write_bytes(first)
    (directory / "ignored.der").write_bytes(b"ignored")
    (directory / "ignored.key").write_bytes(_pkcs8_key())
    environ = _environment(tmp_path)
    environ[DIRECTORY_SETTING] = str(directory)

    bundle = install_custom_ca_bundle(None, environ)

    assert bundle is not None
    assert bundle == tmp_path / "scratch" / BUNDLE_NAME
    assert stat.S_IMODE(bundle.stat().st_mode) == 0o600
    content = bundle.read_bytes()
    public_roots = Path(certifi.where()).read_bytes()
    assert content.startswith(public_roots)
    remainder = content[len(public_roots) :]
    assert remainder.index(first) < remainder.index(second)
    assert b"ignored" not in remainder
    assert b"PRIVATE KEY" not in remainder


def test_each_trust_variable_points_at_the_bundle(tmp_path: Path) -> None:
    directory = tmp_path / "ca"
    directory.mkdir()
    (directory / "ca.pem").write_bytes(_ca_certificate("ca"))
    environ = _environment(tmp_path)

    bundle = install_custom_ca_bundle(directory, environ)

    assert set(TRUST_VARIABLES) == {"SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "AWS_CA_BUNDLE", "INFRAHUB_TLS_CA_FILE"}
    for name in TRUST_VARIABLES:
        assert environ[name] == str(bundle)


def test_a_trust_variable_that_is_already_set_keeps_its_value(tmp_path: Path) -> None:
    directory = tmp_path / "ca"
    directory.mkdir()
    (directory / "ca.pem").write_bytes(_ca_certificate("ca"))
    environ = _environment(tmp_path)
    environ["REQUESTS_CA_BUNDLE"] = "/operator/chosen.pem"

    bundle = install_custom_ca_bundle(directory, environ)

    assert environ["REQUESTS_CA_BUNDLE"] == "/operator/chosen.pem"
    assert environ["SSL_CERT_FILE"] == str(bundle)


@pytest.mark.parametrize("key", [_pkcs8_key, _traditional_key], ids=["pkcs8", "rsa-traditional"])
def test_a_file_holding_a_private_key_stops_without_its_content(tmp_path: Path, key: Callable[[], bytes]) -> None:
    directory = tmp_path / "ca"
    directory.mkdir()
    certificate = _ca_certificate("ca")
    key_bytes = key()
    (directory / "bundle.pem").write_bytes(certificate + key_bytes)
    environ = _environment(tmp_path)
    before = dict(environ)

    with pytest.raises(CustomCertificateError) as raised:
        install_custom_ca_bundle(directory, environ)

    text = str(raised.value)
    assert text == "CA certificate file bundle.pem holds a private key"
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    for line in (certificate + key_bytes).splitlines():
        if line.startswith(b"-----"):
            continue
        assert line.decode() not in text
    assert environ == before
    assert not (tmp_path / "scratch" / BUNDLE_NAME).exists()


def test_a_file_that_ssl_rejects_names_the_file_and_the_error_class_only(tmp_path: Path) -> None:
    directory = tmp_path / "ca"
    directory.mkdir()
    (directory / "a-good.pem").write_bytes(_ca_certificate("good"))
    canary = "unparseable-certificate-canary"
    (directory / "b-broken.crt").write_text(
        f"-----BEGIN CERTIFICATE-----\n{canary}\n-----END CERTIFICATE-----\n", encoding="utf-8"
    )
    environ = _environment(tmp_path)
    before = dict(environ)

    with pytest.raises(CustomCertificateError) as raised:
        install_custom_ca_bundle(directory, environ)

    assert str(raised.value) == "CA certificate file b-broken.crt cannot be loaded: SSLError"
    assert raised.value.__cause__ is None
    assert raised.value.__suppress_context__
    assert environ == before
    assert not (tmp_path / "scratch" / BUNDLE_NAME).exists()


def test_the_log_record_holds_only_the_count_and_the_bundle_path(tmp_path: Path) -> None:
    directory = tmp_path / "ca"
    directory.mkdir()
    (directory / "a.pem").write_bytes(_ca_certificate("a"))
    (directory / "b.crt").write_bytes(_ca_certificate("b"))
    environ = _environment(tmp_path)

    with capture_logs() as records:
        bundle = install_custom_ca_bundle(directory, environ)

    assert records == [
        {
            "event": "custom CA bundle installed",
            "log_level": "info",
            "certificates": 2,
            "bundle": str(bundle),
        }
    ]
