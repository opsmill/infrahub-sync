"""Add an operator's CA certificates to the trust store of a Sync process.

The Compose deployment mounts an optional, read-only directory of PEM CA
certificates. Each Sync entry point calls :func:`install_custom_ca_bundle` before it
builds any client: the function writes the public roots followed by the operator's
files to one bundle, and points the trust variables that ``requests``, ``httpx``,
botocore and the Infrahub SDK read at that bundle. Child processes inherit the
variables through the environment.

A CA certificate is public, but a file in that directory could hold a private key by
mistake. Neither the key nor any other file content reaches an error or a log record:
errors name the file, and the log records hold only a count, a path, or the name of a
trust variable that was already set, never its value.
"""

from __future__ import annotations

import contextlib
import os
import ssl
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import certifi
import structlog

if TYPE_CHECKING:
    from collections.abc import MutableMapping

DIRECTORY_SETTING = "INFRAHUB_SYNC_CA_DIRECTORY"
BUNDLE_NAME = "infrahub-sync-ca-bundle.pem"
CERTIFICATE_SUFFIXES = frozenset({".pem", ".crt"})
TRUST_VARIABLES = ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "AWS_CA_BUNDLE", "INFRAHUB_TLS_CA_FILE")

_PEM_BEGIN = b"-----BEGIN "
_CERTIFICATE_HEADER = b"-----BEGIN CERTIFICATE-----"
_PRIVATE_KEY_MARKER = b"PRIVATE KEY-----"

logger = structlog.get_logger(__name__)


class CustomCertificateError(ValueError):
    """A file in the CA directory cannot be trusted; the text names the file only."""


def _certificate_files(directory: Path) -> list[Path]:
    """Return the directory's ``.pem`` and ``.crt`` files, in any letter case, in name order."""
    try:
        if not directory.is_dir():
            return []
        return sorted(
            (
                entry
                for entry in directory.iterdir()
                if entry.suffix.lower() in CERTIFICATE_SUFFIXES and entry.is_file()
            ),
            key=lambda entry: entry.name,
        )
    except OSError:
        # Only a fixed text: the error carries the path, and the reason is the operator's to check.
        msg = "CA certificate directory cannot be read"
        raise CustomCertificateError(msg) from None


def _read(path: Path) -> bytes:
    """Return one file's bytes, refusing an unreadable file by its name only."""
    try:
        return path.read_bytes()
    except OSError:
        msg = f"CA certificate file {path.name} cannot be read"
        raise CustomCertificateError(msg) from None


def _pem_headers(content: bytes) -> list[bytes]:
    """Return only the PEM ``BEGIN`` lines of a file, never its body."""
    return [line.strip() for line in content.splitlines() if line.strip().startswith(_PEM_BEGIN)]


def _check_file(path: Path, content: bytes) -> int:
    """Refuse a private key or a file ``ssl`` cannot load; return its certificate count.

    The key marker is searched in the whole file, not only at the start of a line:
    ``ssl`` skips a line that does not open a PEM block, so a key it ignores would
    still be copied into the bundle.
    """
    if _PRIVATE_KEY_MARKER in content:
        msg = f"CA certificate file {path.name} holds a private key"
        raise CustomCertificateError(msg)
    headers = _pem_headers(content)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    try:
        context.load_verify_locations(cafile=str(path))
    except OSError as error:
        # Only the class: an ssl message can quote part of what it failed to parse.
        msg = f"CA certificate file {path.name} cannot be loaded: {type(error).__name__}"
        raise CustomCertificateError(msg) from None
    return sum(1 for header in headers if header == _CERTIFICATE_HEADER)


def _write_bundle(path: Path, parts: list[bytes]) -> None:
    """Write the bundle with mode ``0600``, replacing any bundle from an earlier start.

    The content goes to a new file that ``mkstemp`` creates exclusively, and a rename
    then puts it at ``path``. A rename replaces the name itself, so a link someone
    planted at ``path`` in a shared temporary directory is replaced, never followed.
    """
    descriptor, temporary = tempfile.mkstemp(prefix=f".{BUNDLE_NAME}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as bundle:
            for part in parts:
                bundle.write(part)
                if not part.endswith(b"\n"):
                    bundle.write(b"\n")
        Path(temporary).replace(path)
    except BaseException:
        with contextlib.suppress(OSError):
            Path(temporary).unlink()
        raise


def install_custom_ca_bundle(
    directory: Path | None = None,
    environ: MutableMapping[str, str] | None = None,
) -> Path | None:
    """Trust the CA certificates of ``directory`` in addition to the public roots.

    Args:
        directory: The CA directory. When ``None``, the directory named by
            ``INFRAHUB_SYNC_CA_DIRECTORY`` in ``environ``; when that is unset too,
            nothing changes.
        environ: The environment to read and update; ``os.environ`` by default.

    Returns:
        The bundle path, or ``None`` when the directory is absent or holds no
        ``.pem`` or ``.crt`` file, in which case no variable and no file changes.

    Raises:
        CustomCertificateError: The directory or a file in it cannot be read, a
            file holds a private key, or ``ssl`` cannot load a file. Nothing is
            written and no variable changes.
    """
    environment = os.environ if environ is None else environ
    if directory is None:
        setting = environment.get(DIRECTORY_SETTING)
        if not setting:
            return None
        directory = Path(setting)

    files = _certificate_files(directory)
    if not files:
        if directory.is_dir():
            logger.info("custom CA directory holds no certificate file", directory=str(directory))
        return None

    contents: list[bytes] = []
    certificates = 0
    for path in files:
        content = _read(path)
        certificates += _check_file(path, content)
        contents.append(content)

    scratch = environment.get("TMPDIR") or tempfile.gettempdir()
    bundle = Path(scratch) / BUNDLE_NAME
    _write_bundle(bundle, [Path(certifi.where()).read_bytes(), *contents])

    for name in TRUST_VARIABLES:
        if name in environment:
            # The name only, never the value.
            logger.warning("trust variable already set; it overrides the custom CA directory", variable=name)
        else:
            environment[name] = str(bundle)

    logger.info("custom CA bundle installed", certificates=certificates, bundle=str(bundle))
    return bundle
