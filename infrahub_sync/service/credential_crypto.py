"""Encryption of stored credential values with the key that only the Sync API holds (research R3).

Each revision of a credential stores one AES-256-GCM ciphertext of the JSON object of its set
fields, with a random 12-byte nonce. The associated data binds the ciphertext to its credential
and revision, so a ciphertext copied to another row or revision does not decrypt. The key
identifier stored with each revision lets a wrong key be reported before any decryption.

``cryptography`` belongs to the ``service`` extra, so this module lives under ``service/`` and
the product store never imports it: the store receives and returns the key identifier, the nonce
and the ciphertext, never a value.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import TYPE_CHECKING, Literal, NamedTuple

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from infrahub_sync.execution import register_secret_values

if TYPE_CHECKING:
    from collections.abc import Mapping

KEY_BYTES = 32
NONCE_BYTES = 12
_KEY_ID_BYTES = 8
_ASSOCIATED_DATA_PREFIX = "infrahub-sync/credential/v1"
_UNDECRYPTABLE_MESSAGE = "the stored value cannot be decrypted with the current key"

DecryptionFailure = Literal["key-mismatch", "authentication-failed", "malformed-value"]
_KEY_MISMATCH: DecryptionFailure = "key-mismatch"
_AUTHENTICATION_FAILED: DecryptionFailure = "authentication-failed"
_MALFORMED_VALUE: DecryptionFailure = "malformed-value"


class CredentialDecryptionError(RuntimeError):
    """A stored value cannot be decrypted with the current key.

    ``reason`` tells the three cases apart for logs and tests: the stored key identifier is not
    the current key's (``key-mismatch``, found before any decryption), the ciphertext does not
    authenticate (``authentication-failed``), or the decrypted text is not an object of strings
    (``malformed-value``). The message is the same for each and holds no value.
    """

    def __init__(self, reason: DecryptionFailure) -> None:
        super().__init__(_UNDECRYPTABLE_MESSAGE)
        self.reason: DecryptionFailure = reason


class EncryptedCredential(NamedTuple):
    """What the store keeps for one revision: the key identifier, the nonce and the ciphertext."""

    key_id: str
    nonce: bytes
    ciphertext: bytes


def key_identifier(key: bytes) -> str:
    """Return the identifier of a key: the first 8 bytes of its SHA-256 digest, in hex."""
    return hashlib.sha256(key).digest()[:_KEY_ID_BYTES].hex()


def _associated_data(credential_id: str, revision: int) -> bytes:
    return f"{_ASSOCIATED_DATA_PREFIX}|{credential_id}|{revision}".encode()


class CredentialCipher:
    """Encrypt and decrypt the values of stored credentials with one 32-byte key.

    The key is the decoded ``encryption_key`` of ``service.settings.CredentialSettings``.
    """

    __slots__ = ("_aead", "_key_id")

    def __init__(self, key: bytes) -> None:
        """Hold the key for AES-256-GCM.

        Raises:
            ValueError: the key is not 32 bytes. The message holds no part of the key.
        """
        if len(key) != KEY_BYTES:
            msg = f"the key of stored credentials must be {KEY_BYTES} bytes"
            raise ValueError(msg)
        self._aead = AESGCM(key)
        self._key_id = key_identifier(key)

    def __repr__(self) -> str:
        """Name the key by its identifier only."""
        return f"CredentialCipher(key_id={self._key_id!r})"

    @property
    def key_id(self) -> str:
        """The identifier of the current key."""
        return self._key_id

    def encrypt(self, credential_id: str, revision: int, fields: Mapping[str, str]) -> EncryptedCredential:
        """Encrypt the JSON object of ``fields`` for one revision of one credential."""
        nonce = os.urandom(NONCE_BYTES)
        plaintext = json.dumps(dict(fields), sort_keys=True, separators=(",", ":")).encode()
        ciphertext = self._aead.encrypt(nonce, plaintext, _associated_data(credential_id, revision))
        return EncryptedCredential(key_id=self._key_id, nonce=nonce, ciphertext=ciphertext)

    def decrypt(
        self, credential_id: str, revision: int, *, key_id: str, nonce: bytes, ciphertext: bytes
    ) -> dict[str, str]:
        """Return the fields of one revision, after adding each value to the redaction registry.

        The call must run inside `execution.redaction_scope`: the scope of a Sync API request, or
        the scope of a run. A value shorter than `execution.MIN_SECRET_LENGTH` is not added.

        Raises:
            CredentialDecryptionError: the key identifier is not the current key's, checked before
                any decryption; the ciphertext does not authenticate for this credential and
                revision; or the decrypted text is not a JSON object of strings.
            NoRedactionScopeError: no redaction registry is bound (`execution.redaction_scope`),
                so the values would be in no redaction list. No value is returned.
        """
        if key_id != self._key_id:
            raise CredentialDecryptionError(_KEY_MISMATCH)
        try:
            plaintext = self._aead.decrypt(nonce, ciphertext, _associated_data(credential_id, revision))
        except (InvalidTag, ValueError):
            # `ValueError` for a nonce of the wrong length. Neither error holds a value; the
            # chain is dropped anyway, so no later change to them can carry one out.
            raise CredentialDecryptionError(_AUTHENTICATION_FAILED) from None
        try:
            fields = json.loads(plaintext)
        except ValueError:
            # The text of a JSON error quotes the document, which is the value.
            raise CredentialDecryptionError(_MALFORMED_VALUE) from None
        if not isinstance(fields, dict) or not all(
            isinstance(name, str) and isinstance(value, str) for name, value in fields.items()
        ):
            raise CredentialDecryptionError(_MALFORMED_VALUE)
        # Before the values leave this function: every message that the request or the run
        # writes from now on is redacted with them.
        register_secret_values(fields.values())
        return fields
