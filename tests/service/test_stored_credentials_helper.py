"""The shared helper of stored credentials creates what the tests of spec 027 build on.

Values are compared through their digests, so a failure message never shows a value.
"""

from __future__ import annotations

import base64
import hashlib
import json
from typing import TYPE_CHECKING

import httpx
import pytest

pytest.importorskip("cryptography")

from infrahub_sync.product_store import local_product_projection
from tests.helpers.stored_credentials import (
    TEST_ENCRYPTION_KEY,
    TEST_ENCRYPTION_KEY_BASE64,
    StoredCredentialApiError,
    create_credentials_through_api,
    create_stored_credential,
    decrypt_stored_credential,
    stored_credentials,  # noqa: F401 - a pytest fixture
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from tests.helpers.stored_credentials import StoredCredentialFactory

# Test values only; they reach no system.
_TOKEN = "helper-test-token-0001"  # noqa: S105 - a test value
_NEXT_TOKEN = "helper-test-token-0002"  # noqa: S105 - a test value


def _digest(fields: Mapping[str, str]) -> str:
    return hashlib.sha256(json.dumps(dict(fields), sort_keys=True).encode()).hexdigest()


def test_the_test_key_is_32_bytes_in_the_text_the_sync_api_takes() -> None:
    assert len(TEST_ENCRYPTION_KEY) == 32
    assert base64.b64decode(TEST_ENCRYPTION_KEY_BASE64, validate=True) == TEST_ENCRYPTION_KEY


def test_the_fixture_creates_credentials_that_decrypt_with_the_test_key(
    tmp_path: Path,
    stored_credentials: StoredCredentialFactory,  # noqa: F811 - the fixture, by its name
) -> None:
    projection = local_product_projection(tmp_path.resolve())
    wanted = {"netbox-lab": {"token": _TOKEN}, "device-login": {"username": "sync-reader", "password": _NEXT_TOKEN}}

    created = stored_credentials(projection, wanted)

    assert {name: credential.current_revision for name, credential in created.items()} == dict.fromkeys(wanted, 1)
    assert created["device-login"].fields == ("password", "username")
    decrypted = {name: _digest(decrypt_stored_credential(projection, name)) for name in wanted}
    assert decrypted == {name: _digest(fields) for name, fields in wanted.items()}


def test_a_second_creation_with_the_same_name_writes_the_next_revision(tmp_path: Path) -> None:
    projection = local_product_projection(tmp_path.resolve())
    create_stored_credential(projection, "netbox-lab", {"token": _TOKEN})

    changed = create_stored_credential(projection, "netbox-lab", {"token": _NEXT_TOKEN})

    assert changed.current_revision == 2
    assert _digest(decrypt_stored_credential(projection, "netbox-lab")) == _digest({"token": _NEXT_TOKEN})
    assert _digest(decrypt_stored_credential(projection, "netbox-lab", 1)) == _digest({"token": _TOKEN})


class _FakeApi:
    """Answer the credential routes of the HTTP contract and keep what was sent."""

    def __init__(self, *, existing: tuple[str, ...] = (), refuse: int | None = None) -> None:
        self.revisions = dict.fromkeys(existing, 1)
        self.refuse = refuse
        self.requests: list[tuple[str, str, dict[str, str]]] = []
        self.keys: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append((request.method, request.url.path, body))
        self.keys.append(request.headers["Idempotency-Key"])
        assert request.headers["Authorization"] == "Bearer helper-admin-token"
        if self.refuse is not None:
            return httpx.Response(self.refuse, json={"error": {"code": "forbidden", "status": self.refuse}})
        if request.method == "POST":
            name = body["name"]
            if name in self.revisions:
                return httpx.Response(409, json={"error": {"code": "credential-exists", "status": 409}})
            self.revisions[name] = 1
            return httpx.Response(201, json={"name": name, "revision": 1})
        name = request.url.path.split("/")[-2]
        self.revisions[name] += 1
        return httpx.Response(200, json={"name": name, "revision": self.revisions[name]})

    def client(self) -> httpx.Client:
        return httpx.Client(base_url="http://sync-api.test/api", transport=httpx.MockTransport(self))


def test_the_api_function_creates_a_credential_and_changes_one_that_exists() -> None:
    api = _FakeApi(existing=("netbox-lab",))

    with api.client() as client:
        resources = create_credentials_through_api(
            "http://sync-api.test",
            "helper-admin-token",
            {"infrahub": {"token": _TOKEN}, "netbox-lab": {"token": _NEXT_TOKEN}},
            client=client,
        )

    assert [(method, path) for method, path, _body in api.requests] == [
        ("POST", "/api/credentials"),
        ("POST", "/api/credentials"),
        ("PUT", "/api/credentials/netbox-lab/value"),
    ]
    assert set(api.requests[0][2]) == {"name", "token"}
    assert set(api.requests[2][2]) == {"token"}
    assert len(set(api.keys)) == 3
    assert resources == {
        "infrahub": {"name": "infrahub", "revision": 1},
        "netbox-lab": {"name": "netbox-lab", "revision": 2},
    }


def test_a_refusal_of_the_api_is_reported_without_a_value() -> None:
    api = _FakeApi(refuse=403)

    with api.client() as client, pytest.raises(StoredCredentialApiError) as caught:
        create_credentials_through_api(
            "http://sync-api.test", "helper-admin-token", {"x": {"token": _TOKEN}}, client=client
        )

    assert (caught.value.status, caught.value.code) == (403, "forbidden")
    leaked = _TOKEN in str(caught.value)
    assert not leaked
