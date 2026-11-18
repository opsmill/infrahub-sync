"""Password hashes and the values a request presents to the Sync API.

A password is stored as an scrypt hash. The stored text names the algorithm and carries the cost
parameters, the salt and the derived key, so a hash made with other parameters still verifies::

    scrypt$<N>$<r>$<p>$<salt>$<derived key>

The salt and the derived key are base64 text without padding.

An API token and a session token are random values. The store holds only their SHA-256 digest:
a random 256-bit value cannot be guessed, so it needs no slow hash.

No function of this module logs a password, a token value, a digest or a hash, or puts one in an
exception message.
"""

from __future__ import annotations

import binascii
import hashlib
import hmac
import secrets
import threading
from base64 import b64decode, b64encode
from contextlib import contextmanager
from typing import TYPE_CHECKING
from weakref import WeakValueDictionary

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

API_TOKEN_PREFIX = "ifs_pat_"
SESSION_TOKEN_PREFIX = "ifs_ses_"
TOKEN_PREFIXES = (API_TOKEN_PREFIX, SESSION_TOKEN_PREFIX)
# The random part of a token value: 32 bytes, written as 43 URL-safe characters.
TOKEN_RANDOM_BYTES = 32

# The scrypt setting N=2^15, r=8, p=3 of the OWASP Password Storage Cheat Sheet, confirmed on
# 2026-11-17 (ADR 0049). One verification needs about 32 MiB.
SCRYPT_ALGORITHM = "scrypt"
SCRYPT_N = 2**15
SCRYPT_R = 8
SCRYPT_P = 3
SALT_BYTES = 16
DERIVED_KEY_BYTES = 32
# `hashlib.scrypt` refuses the setting above without an explicit `maxmem`. The same value is the
# ceiling for a stored hash: a row whose parameters need more memory is not verified, so a
# corrupted or hostile row cannot exhaust the memory of the process.
SCRYPT_MAXMEM = 64 * 1024 * 1024
# `p` costs processor time, not memory, so it has its own ceiling.
_SCRYPT_MAX_P = 16
_SCRYPT_MAX_KEY_BYTES = 128
_HASH_FIELDS = 6

VERIFICATION_LIMIT = 2
VERIFICATION_WAIT_SECONDS = 5.0


_PASSWORD_ENCODING_MESSAGE = "the password holds a character that UTF-8 cannot encode"
_NOT_ADMITTED_MESSAGE = "the password was not checked: the caller withdrew the check once a place was held"


class SignInBusyError(RuntimeError):
    """A password verification waited too long for one of the places of the gate."""


class VerificationNotAdmittedError(RuntimeError):
    """The caller's ``admit`` check refused a verification once a place of the gate was held."""


class PasswordEncodingError(ValueError):
    """A password holds a character that UTF-8 cannot encode, such as a lone surrogate.

    The message never holds the password: ``UnicodeEncodeError`` keeps the whole text in its
    ``object`` attribute, so it is neither the cause nor the context of this error.
    """


def _encode(value: bytes) -> str:
    return b64encode(value).decode("ascii").rstrip("=")


def _decode(text: str) -> bytes:
    return b64decode(text + "=" * (-len(text) % 4), validate=True)


def scrypt_memory_bytes(n: int, r: int, p: int) -> int:
    """Return the memory that one scrypt computation with these parameters needs."""
    return 128 * r * (n + p + 2)


def is_encodable(text: str) -> bool:
    """Return whether UTF-8 can encode the text: false for a lone surrogate, which JSON allows."""
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _derive(password: str, salt: bytes, *, n: int, r: int, p: int, length: int) -> bytes:
    if not is_encodable(password):
        raise PasswordEncodingError(_PASSWORD_ENCODING_MESSAGE)
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, maxmem=SCRYPT_MAXMEM, dklen=length)


def hash_password(password: str, *, n: int = SCRYPT_N, r: int = SCRYPT_R, p: int = SCRYPT_P) -> str:
    """Return the text to store for a password, made with a new random salt.

    Raises:
        PasswordEncodingError: UTF-8 cannot encode the password.
    """
    salt = secrets.token_bytes(SALT_BYTES)
    key = _derive(password, salt, n=n, r=r, p=p, length=DERIVED_KEY_BYTES)
    return f"{SCRYPT_ALGORITHM}${n}${r}${p}${_encode(salt)}${_encode(key)}"


def _parse(stored_hash: str) -> tuple[int, int, int, bytes, bytes] | None:
    """Return the parameters, the salt and the key of a stored hash that is safe to verify."""
    fields = stored_hash.split("$")
    if len(fields) != _HASH_FIELDS or fields[0] != SCRYPT_ALGORITHM:
        return None
    try:
        n, r, p = (int(field) for field in fields[1:4])
        salt, key = _decode(fields[4]), _decode(fields[5])
    except (ValueError, binascii.Error):
        return None
    if n < 2 or n & (n - 1) or r < 1 or not 1 <= p <= _SCRYPT_MAX_P:
        return None
    if not salt or not 1 <= len(key) <= _SCRYPT_MAX_KEY_BYTES:
        return None
    if scrypt_memory_bytes(n, r, p) > SCRYPT_MAXMEM:
        return None
    return n, r, p, salt, key


def is_readable_hash(stored_hash: str) -> bool:
    """Return whether a stored text is a hash of this module whose parameters are within the ceilings."""
    return _parse(stored_hash) is not None


def verify_password(password: str, stored_hash: str) -> bool:
    """Return whether the stored hash was made from this password.

    A text that is not a hash of this module, or whose parameters are above the ceilings, verifies
    no password. The comparison of the derived keys takes the same time whatever the keys hold.
    """
    parsed = _parse(stored_hash)
    if parsed is None:
        return False
    n, r, p, salt, key = parsed
    try:
        derived = _derive(password, salt, n=n, r=r, p=p, length=len(key))
    except ValueError:
        # OpenSSL refused the parameters, or UTF-8 cannot encode the password. The message is not
        # kept: it describes a stored row or holds the password.
        return False
    return hmac.compare_digest(derived, key)


_NO_ACCOUNT_HASH: list[str] = []
_NO_ACCOUNT_HASH_LOCK = threading.Lock()


def no_account_hash() -> str:
    """Return a fixed hash to verify against when a name has no account.

    It is made once for the process, from a random password that is not kept, so no password
    verifies it. A sign-in with an unknown name then takes the time of a sign-in with a known one.
    The lock makes requests that arrive together wait for the one computation: each would
    otherwise compute its own hash, outside the places of the gate, with 32 MiB each.
    """
    with _NO_ACCOUNT_HASH_LOCK:
        if not _NO_ACCOUNT_HASH:
            _NO_ACCOUNT_HASH.append(hash_password(secrets.token_urlsafe(TOKEN_RANDOM_BYTES)))
        return _NO_ACCOUNT_HASH[0]


def new_api_token() -> str:
    """Return a new API token value: the prefix ``ifs_pat_`` and 32 random bytes."""
    return API_TOKEN_PREFIX + secrets.token_urlsafe(TOKEN_RANDOM_BYTES)


def new_session_token() -> str:
    """Return a new session token value: the prefix ``ifs_ses_`` and 32 random bytes."""
    return SESSION_TOKEN_PREFIX + secrets.token_urlsafe(TOKEN_RANDOM_BYTES)


def digest(value: str) -> str:
    """Return the SHA-256 digest, in hexadecimal, that the store holds for a token value."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def contains_token_prefix(text: str) -> bool:
    """Return whether a text contains the prefix of an API token or of a session token."""
    return any(prefix in text for prefix in TOKEN_PREFIXES)


class VerificationGate:
    """Let a fixed number of password verifications run at the same time in this process.

    Two verifications need about 64 MiB together, whatever the number of sign-in requests. A
    verification that waits longer than ``wait_seconds`` for a place is refused.
    """

    def __init__(self, *, limit: int = VERIFICATION_LIMIT, wait_seconds: float = VERIFICATION_WAIT_SECONDS) -> None:
        self._places = threading.BoundedSemaphore(limit)
        self._wait_seconds = wait_seconds

    @contextmanager
    def place(self) -> Iterator[None]:
        """Hold one place of the gate for the duration of the block.

        Raises:
            SignInBusyError: no place became free within the wait.
        """
        if not self._places.acquire(timeout=self._wait_seconds):
            msg = "too many password verifications are running; try again"
            raise SignInBusyError(msg)
        try:
            yield
        finally:
            self._places.release()

    def verify(self, password: str, stored_hash: str, *, admit: Callable[[], bool] | None = None) -> bool:
        """Verify a password while holding one place of the gate.

        ``admit``, when given, is called once the place is held and immediately before the check.
        When it returns false, the password is not checked. A sign-in uses it to read the refusal
        of its name again after it waited for a place.

        Raises:
            SignInBusyError: no place became free within the wait.
            VerificationNotAdmittedError: ``admit`` returned false; no password was checked.
        """
        with self.place():
            if admit is not None and not admit():
                raise VerificationNotAdmittedError(_NOT_ADMITTED_MESSAGE)
            return verify_password(password, stored_hash)

    def hash(self, password: str) -> str:
        """Return the text to store for a new password, computed while holding one place of the gate.

        A hash costs the memory of a verification, so both share the places of the gate.

        Raises:
            SignInBusyError: no place became free within the wait.
        """
        with self.place():
            return hash_password(password)


# The gate of the process: every sign-in route verifies through it, and every route that stores a
# new password hashes through it.
PASSWORD_VERIFICATION_GATE = VerificationGate()

# One lock for each lower-case name whose password is being checked in this process. A lock leaves
# the map when no request holds it, so the map does not grow with the names that were tried.
_NAME_LOCKS: WeakValueDictionary[str, threading.Lock] = WeakValueDictionary()
_NAME_LOCKS_GUARD = threading.Lock()


@contextmanager
def one_check_per_name(key: str) -> Iterator[None]:
    """Let one password check for this lower-case name run at a time in this process, with its count.

    A burst of sign-ins for one name then reads the refusal, checks the password and counts the
    failure one after the other: a refusal that the fifth failure starts applies to the next check
    of the process. The lock is per process; several processes each run one check at a time.

    Raises:
        SignInBusyError: the lock did not become free within the wait of the gate.
    """
    with _NAME_LOCKS_GUARD:
        lock = _NAME_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _NAME_LOCKS[key] = lock
    if not lock.acquire(timeout=VERIFICATION_WAIT_SECONDS):
        msg = "another password check for this name is running; try again"
        raise SignInBusyError(msg)
    try:
        yield
    finally:
        lock.release()
