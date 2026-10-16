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


def test_a_file_with_an_indented_private_key_header_stops(tmp_path: Path) -> None:
    directory = tmp_path / "ca"
    directory.mkdir()
    indented = b"".join(b"  " + line + b"\n" for line in _pkcs8_key().splitlines())
    (directory / "indented.pem").write_bytes(_ca_certificate("ca") + indented)
    environ = _environment(tmp_path)
    before = dict(environ)

    with pytest.raises(CustomCertificateError) as raised:
        install_custom_ca_bundle(directory, environ)

    assert str(raised.value) == "CA certificate file indented.pem holds a private key"
    assert environ == before
    assert not (tmp_path / "scratch" / BUNDLE_NAME).exists()


def test_a_private_key_marker_anywhere_in_a_file_stops(tmp_path: Path) -> None:
    """`ssl` skips a line that does not start a PEM block, so the key body would reach the bundle."""
    directory = tmp_path / "ca"
    directory.mkdir()
    canary = "mid-line-private-key-canary"
    (directory / "hidden.pem").write_bytes(
        _ca_certificate("ca") + f"note -----BEGIN PRIVATE KEY-----\n{canary}\n".encode()
    )
    environ = _environment(tmp_path)

    with pytest.raises(CustomCertificateError) as raised:
        install_custom_ca_bundle(directory, environ)

    assert str(raised.value) == "CA certificate file hidden.pem holds a private key"
    assert canary not in str(raised.value)
    assert not (tmp_path / "scratch" / BUNDLE_NAME).exists()


def test_an_unreadable_directory_stops_without_the_os_error_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "ca"
    directory.mkdir()
    (directory / "ca.pem").write_bytes(_ca_certificate("ca"))
    canary = "directory-os-error-canary"

    def refuse(_self: Path) -> object:
        raise PermissionError(13, canary, str(directory))

    monkeypatch.setattr(Path, "iterdir", refuse)
    environ = _environment(tmp_path)
    before = dict(environ)

    with pytest.raises(CustomCertificateError) as raised:
        install_custom_ca_bundle(directory, environ)

    assert str(raised.value) == "CA certificate directory cannot be read"
    assert raised.value.__cause__ is None
    assert raised.value.__suppress_context__
    assert canary not in str(raised.value)
    assert environ == before
    assert not (tmp_path / "scratch" / BUNDLE_NAME).exists()


def test_an_unreadable_file_stops_naming_the_file_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    directory = tmp_path / "ca"
    directory.mkdir()
    (directory / "a-good.pem").write_bytes(_ca_certificate("good"))
    (directory / "b-locked.crt").write_bytes(_ca_certificate("locked"))
    canary = "file-os-error-canary"
    read_bytes = Path.read_bytes

    def refuse_one(self: Path) -> bytes:
        if self.name == "b-locked.crt":
            raise PermissionError(13, canary, str(self))
        return read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", refuse_one)
    environ = _environment(tmp_path)
    before = dict(environ)

    with pytest.raises(CustomCertificateError) as raised:
        install_custom_ca_bundle(directory, environ)

    assert str(raised.value) == "CA certificate file b-locked.crt cannot be read"
    assert raised.value.__cause__ is None
    assert raised.value.__suppress_context__
    assert environ == before
    assert not (tmp_path / "scratch" / BUNDLE_NAME).exists()


def test_upper_case_suffixes_are_certificate_files_too(tmp_path: Path) -> None:
    """The suffix match ignores case, so a `.PEM` or `.CRT` file from another system is trusted."""
    directory = tmp_path / "ca"
    directory.mkdir()
    upper = _ca_certificate("upper")
    mixed = _ca_certificate("mixed")
    (directory / "a-upper.PEM").write_bytes(upper)
    (directory / "b-mixed.Crt").write_bytes(mixed)

    bundle = install_custom_ca_bundle(directory, _environment(tmp_path))

    assert bundle is not None
    content = bundle.read_bytes()
    assert upper in content
    assert mixed in content


def test_an_explicit_directory_wins_over_the_setting(tmp_path: Path) -> None:
    chosen = tmp_path / "chosen"
    chosen.mkdir()
    certificate = _ca_certificate("chosen")
    (chosen / "ca.pem").write_bytes(certificate)
    ignored = tmp_path / "ignored"
    ignored.mkdir()
    (ignored / "ca.pem").write_bytes(_pkcs8_key())
    environ = _environment(tmp_path)
    environ[DIRECTORY_SETTING] = str(ignored)

    bundle = install_custom_ca_bundle(chosen, environ)

    assert bundle is not None
    assert certificate in bundle.read_bytes()


def test_an_empty_trust_variable_counts_as_set_and_keeps_its_value(tmp_path: Path) -> None:
    """Only an absent variable is pointed at the bundle; an operator's empty value is left alone."""
    directory = tmp_path / "ca"
    directory.mkdir()
    (directory / "ca.pem").write_bytes(_ca_certificate("ca"))
    environ = _environment(tmp_path)
    environ["SSL_CERT_FILE"] = ""

    bundle = install_custom_ca_bundle(directory, environ)

    assert "SSL_CERT_FILE" in environ
    assert not environ["SSL_CERT_FILE"]
    assert environ["REQUESTS_CA_BUNDLE"] == str(bundle)


def test_a_readable_bundle_from_an_earlier_start_is_replaced_with_mode_0600(tmp_path: Path) -> None:
    directory = tmp_path / "ca"
    directory.mkdir()
    certificate = _ca_certificate("ca")
    (directory / "ca.pem").write_bytes(certificate)
    environ = _environment(tmp_path)
    earlier = tmp_path / "scratch" / BUNDLE_NAME
    earlier.write_bytes(b"stale bundle")
    earlier.chmod(0o644)

    bundle = install_custom_ca_bundle(directory, environ)

    assert bundle is not None
    assert bundle == earlier
    assert stat.S_IMODE(bundle.stat().st_mode) == 0o600
    assert b"stale bundle" not in bundle.read_bytes()
    assert certificate in bundle.read_bytes()


def test_a_planted_link_at_the_bundle_path_is_replaced_not_followed(tmp_path: Path) -> None:
    """In a shared temporary directory another user could plant the bundle name as a link."""
    directory = tmp_path / "ca"
    directory.mkdir()
    (directory / "ca.pem").write_bytes(_ca_certificate("ca"))
    environ = _environment(tmp_path)
    target = tmp_path / "target.txt"
    target.write_bytes(b"left alone")
    (tmp_path / "scratch" / BUNDLE_NAME).symlink_to(target)

    bundle = install_custom_ca_bundle(directory, environ)

    assert bundle is not None
    assert not bundle.is_symlink()
    assert stat.S_IMODE(bundle.stat().st_mode) == 0o600
    assert target.read_bytes() == b"left alone"
    assert sorted(entry.name for entry in (tmp_path / "scratch").iterdir()) == [BUNDLE_NAME]
