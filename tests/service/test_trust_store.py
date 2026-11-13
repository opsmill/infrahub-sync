"""The operator's CA bundle is trusted next to the public roots, and refused when unsafe (spec 021 US3)."""

from __future__ import annotations

import datetime
import logging
import os
import stat
from pathlib import Path

import pytest

pytest.importorskip("cryptography")

import certifi
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from infrahub_sync.service import trust_store
from infrahub_sync.service.trust_store import TRUST_VARIABLES, TrustStoreError, install_custom_ca_bundle

KEY_CANARY = "trust-store-key-canary"


def _ca_certificate(name: str) -> bytes:
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = datetime.datetime.now(datetime.timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    return certificate.public_bytes(serialization.Encoding.PEM)


def _private_key() -> bytes:
    return ec.generate_private_key(ec.SECP256R1()).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )


@pytest.fixture(autouse=True)
def _clean_trust_variables(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name in TRUST_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TMPDIR", str(tmp_path / "scratch"))
    (tmp_path / "scratch").mkdir()


def test_an_absent_directory_changes_nothing(tmp_path: Path) -> None:
    assert install_custom_ca_bundle(tmp_path / "absent") is None
    assert not any(name in os.environ for name in TRUST_VARIABLES)


def test_an_empty_directory_logs_one_line_and_changes_nothing(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    directory = tmp_path / "ca"
    directory.mkdir()

    with caplog.at_level(logging.INFO):
        assert install_custom_ca_bundle(directory) is None

    assert [record.getMessage() for record in caplog.records] == [f"no CA certificate file in {directory}"]
    assert not any(name in os.environ for name in TRUST_VARIABLES)


def test_certificates_become_one_bundle_after_the_public_roots(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    directory = tmp_path / "ca"
    directory.mkdir()
    first, second = _ca_certificate("Spec 021 Test CA one"), _ca_certificate("Spec 021 Test CA two")
    (directory / "one.pem").write_bytes(first)
    (directory / "two.crt").write_bytes(second)
    (directory / "notes.txt").write_text("not a certificate file", encoding="utf-8")

    with caplog.at_level(logging.INFO):
        bundle = install_custom_ca_bundle(directory)

    assert bundle is not None
    content = bundle.read_bytes()
    roots = Path(certifi.where()).read_bytes()
    assert content.startswith(roots)
    assert first in content
    assert second in content
    assert stat.S_IMODE(bundle.stat().st_mode) == 0o600
    for name in TRUST_VARIABLES:
        assert os.environ[name] == str(bundle)
    assert [record.getMessage() for record in caplog.records] == [f"loaded 2 CA certificate(s); bundle {bundle}"]


def test_an_already_set_trust_variable_is_kept_and_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    directory = tmp_path / "ca"
    directory.mkdir()
    (directory / "ca.pem").write_bytes(_ca_certificate("Spec 021 Test CA"))
    monkeypatch.setenv("SSL_CERT_FILE", "/operator/value-canary.pem")

    with caplog.at_level(logging.INFO):
        bundle = install_custom_ca_bundle(directory)

    assert os.environ["SSL_CERT_FILE"] == "/operator/value-canary.pem"
    assert os.environ["REQUESTS_CA_BUNDLE"] == str(bundle)
    warnings = [record.getMessage() for record in caplog.records if record.levelno == logging.WARNING]
    assert warnings == ["SSL_CERT_FILE is already set and overrides the CA bundle"]
    assert "value-canary" not in caplog.text


def test_a_file_holding_a_private_key_is_refused_by_name_only(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    directory = tmp_path / "ca"
    directory.mkdir()
    (directory / "ca.pem").write_bytes(_ca_certificate("Spec 021 Test CA"))
    (directory / "leaked.pem").write_bytes(_ca_certificate("Spec 021 Test CA") + _private_key())

    with caplog.at_level(logging.INFO), pytest.raises(TrustStoreError) as refusal:
        install_custom_ca_bundle(directory)

    assert str(refusal.value) == "leaked.pem holds a private key; the CA directory takes public certificates only"
    assert "PRIVATE KEY" not in caplog.text + str(refusal.value)
    assert not any(name in os.environ for name in TRUST_VARIABLES)


def test_a_file_that_is_not_a_certificate_is_refused_with_the_ssl_error_class(tmp_path: Path) -> None:
    directory = tmp_path / "ca"
    directory.mkdir()
    (directory / "broken.pem").write_text(f"-----BEGIN CERTIFICATE-----\n{KEY_CANARY}\n-----END CERTIFICATE-----\n")

    with pytest.raises(TrustStoreError) as refusal:
        install_custom_ca_bundle(directory)

    assert str(refusal.value).startswith("broken.pem is not a readable CA certificate (")
    assert KEY_CANARY not in str(refusal.value)


def test_the_directory_setting_is_read_from_the_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    directory = tmp_path / "ca"
    directory.mkdir()
    (directory / "ca.pem").write_bytes(_ca_certificate("Spec 021 Test CA"))
    monkeypatch.setenv(trust_store.CA_DIRECTORY_ENV, str(directory))

    assert install_custom_ca_bundle() is not None


def test_no_directory_setting_changes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(trust_store.CA_DIRECTORY_ENV, raising=False)

    assert install_custom_ca_bundle() is None


def test_an_unreadable_directory_is_refused_naming_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    directory = tmp_path / "ca"
    directory.mkdir()

    def refuse(_directory: Path) -> list[Path]:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(trust_store, "_certificate_files", refuse)

    with pytest.raises(TrustStoreError) as refusal:
        install_custom_ca_bundle(directory)

    assert str(refusal.value) == (
        f"cannot read the CA directory {directory} (PermissionError); user ID 10001 must be able to list it"
    )
