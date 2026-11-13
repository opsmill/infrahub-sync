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

import logging
import os
import ssl
import tempfile
from pathlib import Path

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


class TrustStoreError(RuntimeError):
    """The CA directory holds a file this process refuses to trust; the message names the file only."""


def _certificate_files(directory: Path) -> list[Path]:
    return sorted(
        path for path in directory.iterdir() if path.is_file() and path.suffix.lower() in CERTIFICATE_SUFFIXES
    )


def _checked(path: Path) -> bytes:
    """Return one file's content after refusing a private key and an unreadable certificate."""
    try:
        content = path.read_bytes()
    except OSError as error:
        msg = f"cannot read {path.name} in the CA directory ({type(error).__name__})"
        raise TrustStoreError(msg) from None
    if _PRIVATE_KEY_MARKER in content:
        msg = f"{path.name} holds a private key; the CA directory takes public certificates only"
        raise TrustStoreError(msg)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    try:
        context.load_verify_locations(cadata=content.decode("ascii"))
    except (ssl.SSLError, UnicodeDecodeError, ValueError) as error:
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
        files = _certificate_files(directory)
    except OSError as error:
        msg = (
            f"cannot read the CA directory {directory} ({type(error).__name__}); user ID 10001 must be able to list it"
        )
        raise TrustStoreError(msg) from None
    if not files:
        logger.info("no CA certificate file in %s", directory)
        return None
    contents = [_checked(path) for path in files]
    count = sum(content.count(_CERTIFICATE_MARKER) for content in contents)
    try:
        roots = Path(certifi.where()).read_bytes()
    except OSError as error:
        msg = f"cannot read the public root certificates ({type(error).__name__})"
        raise TrustStoreError(msg) from None
    try:
        # One file per process: two processes on one host never rewrite each other's bundle.
        # mkstemp creates it with mode 0600.
        descriptor, name = tempfile.mkstemp(prefix=BUNDLE_PREFIX, suffix=".pem")
        bundle = Path(name)
        with os.fdopen(descriptor, "wb") as written:
            written.write(roots if roots.endswith(b"\n") else roots + b"\n")
            for content in contents:
                written.write(content)
    except OSError as error:
        msg = f"cannot write the CA bundle in {tempfile.gettempdir()} ({type(error).__name__})"
        raise TrustStoreError(msg) from None
    for name in TRUST_VARIABLES:
        if os.environ.get(name):
            logger.warning("%s is already set and overrides the CA bundle", name)
            continue
        os.environ[name] = str(bundle)
    logger.info("loaded %d CA certificate(s); bundle %s", count, bundle)
    return bundle
