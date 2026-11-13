"""Trust the operator's CA certificates next to the public roots, in every Sync process.

A Compose deployment mounts the directory `INFRAHUB_SYNC_CA_CERTIFICATES` names, read-only,
at `INFRAHUB_SYNC_CA_DIRECTORY` in `sync-bootstrap`, `sync-api` and `sync-worker`. Each
process calls `install_custom_ca_bundle()` first, before any client exists. It writes one
bundle -- the public roots `certifi` ships, then the operator's files -- to the process's
scratch directory, and points the trust variables every client library reads at it:

- `SSL_CERT_FILE`: `httpx` and Python's default SSL context, which the Infrahub SDK uses
- `REQUESTS_CA_BUNDLE`: `requests`, which the NetBox, Nautobot and ACI clients use
- `AWS_CA_BUNDLE`: botocore, the object store client
- `INFRAHUB_TLS_CA_FILE`: the Infrahub SDK's own setting

Each variable replaces its library's default store rather than adding to it, which is why
the bundle starts from the public roots. A variable the operator already set is kept and
named in a warning. A certificate is public; a file that holds a private key stops the
process, naming the file and printing none of its content. A flow run the worker starts
inherits the worker's environment, so it trusts the same bundle.
"""

from __future__ import annotations

import atexit
import logging
import os
import ssl
import tempfile
from pathlib import Path
from typing import NoReturn

import certifi

logger = logging.getLogger(__name__)
# Importing Prefect sets the root logger to a level that drops INFO before a service's
# `main` runs; the certificate count and bundle path are this module's whole report.
logger.setLevel(logging.INFO)
# Used only when no handler would print this module's lines: the Sync API process configures
# no logging before uvicorn starts.
_FALLBACK_HANDLER = logging.StreamHandler()
_FALLBACK_HANDLER.setFormatter(logging.Formatter("%(levelname)s | infrahub-sync | %(message)s"))

CA_DIRECTORY_ENV = "INFRAHUB_SYNC_CA_DIRECTORY"
TRUST_VARIABLES = ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "AWS_CA_BUNDLE", "INFRAHUB_TLS_CA_FILE")
CERTIFICATE_SUFFIXES = frozenset({".pem", ".crt"})
BUNDLE_PREFIX = "infrahub-sync-ca-bundle-"
_PRIVATE_KEY_MARKER = b"PRIVATE KEY-----"
_CERTIFICATE_MARKER = b"-----BEGIN CERTIFICATE-----"
# A file the bundle does not trust is searched for a private key this many bytes at a time,
# so a large file in the directory never sits in memory whole.
_SCAN_BLOCK_BYTES = 1024 * 1024


class TrustStoreError(RuntimeError):
    """The CA directory holds a file this process refuses to trust; the message names the file only."""


def _regular_files(directory: Path) -> list[Path]:
    return sorted(path for path in directory.iterdir() if path.is_file())


def _read_without_private_key(path: Path) -> bytes:
    """Return one file's content after refusing an unreadable file and a private key."""
    try:
        content = path.read_bytes()
    except OSError as error:
        msg = f"cannot read {path.name} in the CA directory ({type(error).__name__})"
        raise TrustStoreError(msg) from None
    if _PRIVATE_KEY_MARKER in content:
        _refuse_private_key(path)
    return content


def _read_certificate_files(regular: list[Path], files: list[Path]) -> dict[Path, bytes]:
    """Return the content of the certificate files, after searching every file for a private key.

    Every file is searched, whatever its name: a key next to the certificates reaches the
    services through the mount even when it is never trusted. Only certificate files are
    kept; the others are scanned and dropped.
    """
    read: dict[Path, bytes] = {}
    for path in regular:
        if path in files:
            read[path] = _read_without_private_key(path)
        else:
            _scan_for_private_key(path)
    return read


def _refuse_private_key(path: Path) -> NoReturn:
    msg = f"{path.name} holds a private key; the CA directory takes public certificates only"
    raise TrustStoreError(msg)


def _scan_for_private_key(path: Path) -> None:
    """Refuse an unreadable file and a private key, reading the file in blocks and keeping none of it."""
    # Each block is searched together with the end of the one before, so a marker split
    # between two blocks is found.
    overlap = len(_PRIVATE_KEY_MARKER) - 1
    tail = b""
    try:
        with path.open("rb") as stream:
            while block := stream.read(_SCAN_BLOCK_BYTES):
                if _PRIVATE_KEY_MARKER in tail + block:
                    _refuse_private_key(path)
                tail = (tail + block)[-overlap:]
    except OSError as error:
        msg = f"cannot read {path.name} in the CA directory ({type(error).__name__})"
        raise TrustStoreError(msg) from None


def _checked(path: Path, content: bytes) -> bytes:
    """Return one certificate file's content after refusing an unreadable certificate."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    try:
        # Read as a file, the way every client reads the bundle: OpenSSL takes PEM blocks and
        # skips the text around them, which may be UTF-8 (a subject line, for example). A
        # DER file or a file with no certificate is refused.
        context.load_verify_locations(cafile=str(path))
    except (OSError, ValueError) as error:  # ssl.SSLError is an OSError.
        msg = f"{path.name} is not a readable CA certificate ({type(error).__name__})"
        raise TrustStoreError(msg) from None
    return content if content.endswith(b"\n") else content + b"\n"


def install_custom_ca_bundle(directory: Path | None = None) -> Path | None:
    """Trust the CA certificates in `directory` (default: `INFRAHUB_SYNC_CA_DIRECTORY`) in this process.

    Returns the bundle written, or None when there is no directory or no certificate file in it.

    Raises:
        TrustStoreError: when a file holds a private key or is not a readable certificate.
    """
    if not logger.hasHandlers():
        logger.addHandler(_FALLBACK_HANDLER)
    if directory is None:
        configured = os.environ.get(CA_DIRECTORY_ENV)
        if not configured:
            return None
        directory = Path(configured)
    if not directory.is_dir():
        logger.warning(
            "%s names %s, which is not a directory; no CA certificate is loaded", CA_DIRECTORY_ENV, directory
        )
        return None
    try:
        regular = _regular_files(directory)
    except OSError as error:
        msg = (
            f"cannot read the CA directory {directory} ({type(error).__name__}); user ID 10001 must be able to list it"
        )
        raise TrustStoreError(msg) from None
    files = [path for path in regular if path.suffix.lower() in CERTIFICATE_SUFFIXES]
    read = _read_certificate_files(regular, files)
    if not files:
        logger.info("no CA certificate file in %s", directory)
        return None
    contents = [_checked(path, read[path]) for path in files]
    count = sum(content.count(_CERTIFICATE_MARKER) for content in contents)
    try:
        roots = Path(certifi.where()).read_bytes()
    except OSError as error:
        msg = f"cannot read the public root certificates ({type(error).__name__})"
        raise TrustStoreError(msg) from None
    bundle: Path | None = None
    try:
        # One file per process: two processes on one host never rewrite each other's bundle.
        # mkstemp creates it with mode 0600.
        descriptor, name = tempfile.mkstemp(prefix=BUNDLE_PREFIX, suffix=".pem")
        bundle = Path(name)
        with os.fdopen(descriptor, "wb") as written:
            written.write(roots if roots.endswith(b"\n") else roots + b"\n")
            for content in contents:
                written.write(content)
        # Removed when this process ends, so restarts do not leave one file each.
        atexit.register(bundle.unlink, missing_ok=True)
    except OSError as error:
        # A bundle that failed while written is removed, so failed starts leave no file each.
        if bundle is not None:
            bundle.unlink(missing_ok=True)
        msg = f"cannot write the CA bundle in {tempfile.gettempdir()} ({type(error).__name__})"
        raise TrustStoreError(msg) from None
    for name in TRUST_VARIABLES:
        if os.environ.get(name):
            logger.warning("%s is already set and overrides the CA bundle", name)
            continue
        os.environ[name] = str(bundle)
    logger.info("loaded %d CA certificate(s); bundle %s", count, bundle)
    return bundle
