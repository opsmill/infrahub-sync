"""The encryption of stored credential values (research R3).

Values are compared through their digests, so a failure message never shows a value.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import TYPE_CHECKING

import pytest

pytest.importorskip("cryptography")

# pylint: disable-next=wrong-import-position
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# pylint: disable-next=wrong-import-position
from infrahub_sync.service.credential_crypto import (
    NONCE_BYTES,
    CredentialCipher,
    CredentialDecryptionError,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

_KEY = bytes(range(32))
_OTHER_KEY = bytes(range(1, 33))
_CREDENTIAL_ID = "cred_0123456789abcdef0123456789abcdef"
_OTHER_CREDENTIAL_ID = "cred_fedcba9876543210fedcba9876543210"
# Test values only; they reach no system.
_FIELDS = {"username": "sync-reader", "password": "test-password-0001"}


def _digest(fields: Mapping[str, str]) -> str:
    """Return a digest of the fields, so an assertion compares values without showing them."""
    return hashlib.sha256(json.dumps(dict(fields), sort_keys=True).encode()).hexdigest()


def test_a_value_decrypts_to_the_fields_it_was_encrypted_from() -> None:
    cipher = CredentialCipher(_KEY)
    key_id, nonce, ciphertext = cipher.encrypt(_CREDENTIAL_ID, 1, _FIELDS)

    decrypted = cipher.decrypt(_CREDENTIAL_ID, 1, key_id=key_id, nonce=nonce, ciphertext=ciphertext)

    assert _digest(decrypted) == _digest(_FIELDS)
    assert len(nonce) == NONCE_BYTES


def test_the_ciphertext_holds_no_value_in_clear() -> None:
    _key_id, _nonce, ciphertext = CredentialCipher(_KEY).encrypt(_CREDENTIAL_ID, 1, _FIELDS)

    assert all(value.encode() not in ciphertext for value in _FIELDS.values())


def test_two_encryptions_of_the_same_value_use_different_nonces() -> None:
    cipher = CredentialCipher(_KEY)

    first = cipher.encrypt(_CREDENTIAL_ID, 1, _FIELDS)
    second = cipher.encrypt(_CREDENTIAL_ID, 1, _FIELDS)

    assert first.nonce != second.nonce
    assert first.ciphertext != second.ciphertext


@pytest.mark.parametrize(
    ("credential_id", "revision"),
    [
        pytest.param(_OTHER_CREDENTIAL_ID, 1, id="another-credential"),
        pytest.param(_CREDENTIAL_ID, 2, id="another-revision"),
    ],
)
def test_a_ciphertext_copied_to_another_row_does_not_decrypt(credential_id: str, revision: int) -> None:
    cipher = CredentialCipher(_KEY)
    key_id, nonce, ciphertext = cipher.encrypt(_CREDENTIAL_ID, 1, _FIELDS)

    with pytest.raises(CredentialDecryptionError) as raised:
        cipher.decrypt(credential_id, revision, key_id=key_id, nonce=nonce, ciphertext=ciphertext)

    assert raised.value.reason == "authentication-failed"
    assert raised.value.__cause__ is None
    assert raised.value.__suppress_context__


def test_a_wrong_key_is_refused_from_the_key_identifier_before_any_decryption(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key_id, nonce, ciphertext = CredentialCipher(_KEY).encrypt(_CREDENTIAL_ID, 1, _FIELDS)
    calls: list[object] = []

    def _refuse_call(*args: object, **kwargs: object) -> bytes:
        calls.append((args, kwargs))
        msg = "decryption must not be tried with a key of another identifier"
        raise AssertionError(msg)

    monkeypatch.setattr(AESGCM, "decrypt", _refuse_call)

    with pytest.raises(CredentialDecryptionError) as raised:
        CredentialCipher(_OTHER_KEY).decrypt(_CREDENTIAL_ID, 1, key_id=key_id, nonce=nonce, ciphertext=ciphertext)

    assert raised.value.reason == "key-mismatch"
    assert "current key" in str(raised.value)
    assert calls == []


def test_the_key_identifier_is_stable_for_one_key_and_differs_between_keys() -> None:
    first = CredentialCipher(_KEY)
    second = CredentialCipher(_KEY)

    assert re.fullmatch(r"[0-9a-f]{16}", first.key_id)
    assert first.key_id == second.key_id == hashlib.sha256(_KEY).digest()[:8].hex()
    assert first.encrypt(_CREDENTIAL_ID, 1, _FIELDS).key_id == first.key_id
    assert CredentialCipher(_OTHER_KEY).key_id != first.key_id


@pytest.mark.parametrize("length", [0, 16, 31, 33])
def test_a_key_that_is_not_32_bytes_is_refused(length: int) -> None:
    with pytest.raises(ValueError, match="32 bytes"):
        CredentialCipher(bytes(length))


def test_the_cipher_representation_holds_no_key() -> None:
    cipher = CredentialCipher(_KEY)

    assert _KEY.hex() not in repr(cipher)
    assert repr(_KEY) not in repr(cipher)
