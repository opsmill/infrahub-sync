"""Create stored credentials for a test, in a store or through the Sync API (spec 027).

Every test that needs a stored credential uses this module, so that one place holds the test key
and the way a credential is created.

The test key
------------

`TEST_ENCRYPTION_KEY` is a fixed 32-byte key, and `TEST_ENCRYPTION_KEY_BASE64` is the text a
Sync API takes in `INFRAHUB_SYNC_ENCRYPTION_KEY`. The key is public: use it in tests only.

Unit tier: credentials in a store
---------------------------------

`create_stored_credential` writes one credential into a product store, encrypted with the test
key, and returns the stored credential, which holds no value::

    from tests.helpers.stored_credentials import create_stored_credential

    credential = create_stored_credential(projection, "netbox-lab", {"token": "test-token-0001"})

A second call with the same name writes the next revision with the whole new set of fields.

The `stored_credentials` fixture does the same for several credentials. A fixture of a helper
module is not found by pytest on its own: import it in the test module or in a `conftest.py` of
the tests that use it. Do not import it in `tests/conftest.py`: this module needs the `service`
extra to encrypt, and the base install has no such extra. ::

    from tests.helpers.stored_credentials import stored_credentials  # noqa: F401 - a pytest fixture

    def test_it(projection, stored_credentials):
        stored_credentials(projection, {"netbox-lab": {"token": "test-token-0001"}})

`decrypt_stored_credential` reads the fields of a revision back. It runs inside its own redaction
scope, as `CredentialCipher.decrypt` requires, so the values it returns are in no redaction list
after it returns: compare their digests, and never put them in an assertion message.

Compose, development stack and testcontainers: credentials through the API
--------------------------------------------------------------------------

`create_credentials_through_api` creates credentials through the HTTP API of a running Sync API,
with the token of an administrator. It uses plain `httpx`, so it needs no command of the CLI
and no method of `SyncClient`. The Sync API must hold its own encryption key; the function sends
values only. A credential that already exists gets a new revision with the given fields, so the
function can run again against the same Sync API::

    from tests.helpers.stored_credentials import create_credentials_through_api

    create_credentials_through_api(
        "http://127.0.0.1:8000",
        admin_token,
        {"netbox-lab": {"token": netbox_token}, "infrahub": {"token": infrahub_token}},
    )

A refusal raises `StoredCredentialApiError`, whose message holds the status and the error code
of the answer, and no value.
"""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING
from uuid import uuid4

import httpx
import pytest

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from infrahub_sync.product_store import ProductProjection
    from infrahub_sync.product_store.models import StoredCredential

    StoredCredentialFactory = Callable[
        [ProductProjection, Mapping[str, Mapping[str, str]]], dict[str, StoredCredential]
    ]

# A fixed, public key for tests only: the bytes 0 to 31.
TEST_ENCRYPTION_KEY = bytes(range(32))
TEST_ENCRYPTION_KEY_BASE64 = base64.b64encode(TEST_ENCRYPTION_KEY).decode()
# The account identifier that the store records as the author; a test can pass another one.
TEST_CREDENTIAL_ACTOR = "test-stored-credentials"

_API_PREFIX = "/api"
_TIMEOUT_SECONDS = 30.0
_CREATED = 201
_OK = 200
_CONFLICT = 409
_CREDENTIAL_EXISTS = "credential-exists"
_CREATE = "create"
_CHANGE = "change"


class StoredCredentialApiError(RuntimeError):
    """The Sync API refused to create or change a credential. The message holds no value."""

    def __init__(self, action: str, name: str, status: int, code: str) -> None:
        super().__init__(f"the Sync API refused to {action} the credential {name!r}: status {status}, code {code!r}")
        self.status = status
        self.code = code


def create_stored_credential(
    projection: ProductProjection,
    name: str,
    fields: Mapping[str, str],
    *,
    description: str = "",
    actor: str = TEST_CREDENTIAL_ACTOR,
) -> StoredCredential:
    """Write a credential into a store, encrypted with the test key, and return it without its value.

    A credential that already has this name gets its next revision with `fields` as the whole
    new set; its description is not changed.
    """
    # Imported here: `cryptography` belongs to the `service` extra.
    from infrahub_sync.service.credential_crypto import CredentialCipher

    cipher = CredentialCipher(TEST_ENCRYPTION_KEY)

    def encrypt(credential_id: str, revision: int) -> tuple[str, bytes, bytes]:
        return tuple(cipher.encrypt(credential_id, revision, fields))

    if projection.lookup_stored_credential(name) is None:
        return projection.create_stored_credential(
            name=name, description=description, fields=tuple(fields), encrypt=encrypt, actor=actor
        )
    return projection.change_stored_credential_value(name=name, fields=tuple(fields), encrypt=encrypt, actor=actor)


def decrypt_stored_credential(projection: ProductProjection, name: str, revision: int | None = None) -> dict[str, str]:
    """Return the fields of a revision of a credential, the current one by default.

    Raises:
        LookupError: no credential has this name, or it has no such revision.
    """
    from infrahub_sync.execution import redaction_scope
    from infrahub_sync.service.credential_crypto import CredentialCipher

    credential = projection.lookup_stored_credential(name)
    if credential is None:
        msg = f"no stored credential is named {name!r}"
        raise LookupError(msg)
    number = credential.current_revision if revision is None else revision
    stored = projection.lookup_stored_credential_revision(credential.credential_id, number)
    if stored is None:
        msg = f"the stored credential {name!r} has no revision {number}"
        raise LookupError(msg)
    with redaction_scope():
        return CredentialCipher(TEST_ENCRYPTION_KEY).decrypt(
            credential.credential_id, number, key_id=stored.key_id, nonce=stored.nonce, ciphertext=stored.ciphertext
        )


@pytest.fixture
def stored_credentials() -> StoredCredentialFactory:
    """Return a function that writes credentials into a store: `{name: {field: value}}`."""

    def create(
        projection: ProductProjection, credentials: Mapping[str, Mapping[str, str]]
    ) -> dict[str, StoredCredential]:
        return {name: create_stored_credential(projection, name, fields) for name, fields in credentials.items()}

    return create


def _error_code(response: httpx.Response) -> str:
    """Return the error code of an answer, and nothing else of its body."""
    try:
        body = response.json()
    except ValueError:
        return "unknown"
    error = body.get("error") if isinstance(body, dict) else None
    code = error.get("code") if isinstance(error, dict) else None
    return code if isinstance(code, str) else "unknown"


def create_credentials_through_api(
    url: str,
    token: str,
    credentials: Mapping[str, Mapping[str, str]],
    *,
    verify: bool | str = True,
    client: httpx.Client | None = None,
) -> dict[str, dict[str, object]]:
    """Create credentials through a running Sync API and return the credential resources by name.

    `url` is the address of the Sync API without `/api`. `token` is the API token of an
    administrator. `credentials` maps each name to its fields (`username`, `password`, `token`).
    A name that already exists gets a new revision with the given fields. `verify` is passed to
    `httpx` for a Sync API behind TLS. A test passes `client` to send through its own transport;
    its base address must be the address of the Sync API with `/api`.

    Raises:
        StoredCredentialApiError: the Sync API refused a request. The message holds no value.
        httpx.HTTPError: the Sync API could not be reached.
    """
    owned = client is None
    http = client or httpx.Client(base_url=f"{url.rstrip('/')}{_API_PREFIX}", verify=verify, timeout=_TIMEOUT_SECONDS)
    resources: dict[str, dict[str, object]] = {}
    try:
        for name, fields in credentials.items():
            body: dict[str, str] = {"name": name, **fields}
            response = http.post("/credentials", json=body, headers=_headers(token))
            if response.status_code == _CONFLICT and _error_code(response) == _CREDENTIAL_EXISTS:
                response = http.put(f"/credentials/{name}/value", json=dict(fields), headers=_headers(token))
                if response.status_code != _OK:
                    raise StoredCredentialApiError(_CHANGE, name, response.status_code, _error_code(response))
            elif response.status_code != _CREATED:
                raise StoredCredentialApiError(_CREATE, name, response.status_code, _error_code(response))
            resources[name] = response.json()
    finally:
        if owned:
            http.close()
    return resources


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Idempotency-Key": f"test-stored-credential-{uuid4().hex}"}
