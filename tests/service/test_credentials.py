"""Password hashes, token values and the gate that limits concurrent password verifications."""

from __future__ import annotations

import re
import threading
from base64 import urlsafe_b64decode
from hashlib import sha256

import pytest

from infrahub_sync.service import credentials
from infrahub_sync.service.credentials import (
    PasswordEncodingError,
    SignInBusyError,
    VerificationGate,
    VerificationNotAdmittedError,
    contains_token_prefix,
    digest,
    hash_password,
    new_api_token,
    new_session_token,
    no_account_hash,
    verify_password,
)

_PASSWORD = "correct horse battery"  # noqa: S105 - a test value, not a secret.
# Low parameters keep the cases that do not test the setting itself fast.
_FAST = {"n": 2**4, "r": 1, "p": 1}


def test_a_hash_verifies_its_password_and_no_other() -> None:
    stored = hash_password(_PASSWORD)

    assert verify_password(_PASSWORD, stored)
    assert not verify_password(_PASSWORD + "x", stored)
    assert not verify_password("", stored)


def test_a_stored_hash_names_the_algorithm_the_parameters_the_salt_and_the_key() -> None:
    stored = hash_password(_PASSWORD)

    assert re.fullmatch(r"scrypt\$32768\$8\$3\$[A-Za-z0-9+/]{22}\$[A-Za-z0-9+/]{43}", stored)
    assert _PASSWORD not in stored


def test_two_hashes_of_one_password_differ() -> None:
    first = hash_password(_PASSWORD, **_FAST)
    second = hash_password(_PASSWORD, **_FAST)

    assert first != second
    assert verify_password(_PASSWORD, first)
    assert verify_password(_PASSWORD, second)


def test_a_hash_made_with_lower_parameters_still_verifies() -> None:
    stored = hash_password(_PASSWORD, n=2**10, r=4, p=1)

    assert stored.startswith("scrypt$1024$4$1$")
    assert verify_password(_PASSWORD, stored)
    assert not verify_password("another password", stored)


def test_the_parameters_in_the_code_are_not_below_the_recorded_setting() -> None:
    """The setting of ADR 0049: N=2^15, r=8, p=3, with a 16-byte salt."""
    assert credentials.SCRYPT_N >= 2**15
    assert credentials.SCRYPT_R >= 8
    assert credentials.SCRYPT_P >= 3
    assert credentials.SALT_BYTES >= 16
    needed = credentials.scrypt_memory_bytes(credentials.SCRYPT_N, credentials.SCRYPT_R, credentials.SCRYPT_P)
    assert needed <= credentials.SCRYPT_MAXMEM


@pytest.mark.parametrize(
    "stored",
    [
        pytest.param("", id="empty"),
        pytest.param("not a hash", id="no-fields"),
        pytest.param("argon2$32768$8$3$c2FsdA$a2V5", id="another-algorithm"),
        pytest.param("scrypt$32768$8$3$c2FsdA", id="a-missing-field"),
        pytest.param("scrypt$many$8$3$c2FsdA$a2V5", id="a-parameter-that-is-not-a-number"),
        pytest.param("scrypt$1000$8$3$c2FsdA$a2V5", id="n-that-is-not-a-power-of-two"),
        pytest.param("scrypt$16$1$1$!!!$a2V5", id="a-salt-that-is-not-base64"),
        pytest.param("scrypt$16$1$1$c2FsdA$", id="an-empty-key"),
        pytest.param("scrypt$16$0$1$c2FsdA$a2V5", id="r-of-zero"),
    ],
)
def test_a_text_that_is_not_a_stored_hash_verifies_no_password(stored: str) -> None:
    assert not verify_password(_PASSWORD, stored)


# 16 bytes in unpadded Base64, the size of the salt that ``hash_password`` makes.
_SALT_16 = "A" * 22


@pytest.mark.parametrize(
    "parameters",
    [
        pytest.param("1048576$8$1", id="memory-above-the-ceiling"),
        pytest.param("16$1$1000000", id="p-above-the-ceiling"),
    ],
)
def test_a_stored_hash_above_a_ceiling_is_refused_without_being_computed(
    monkeypatch: pytest.MonkeyPatch, parameters: str
) -> None:
    computed: list[object] = []
    monkeypatch.setattr(credentials.hashlib, "scrypt", lambda *args, **kwargs: computed.append((args, kwargs)))

    assert not verify_password(_PASSWORD, f"scrypt${parameters}${_SALT_16}$a2V5")
    assert computed == []


@pytest.mark.parametrize(
    "stored",
    [
        pytest.param("scrypt$16$1$1$c2FsdA$a2V5", id="a-salt-shorter-than-16-bytes"),
        pytest.param(f"scrypt$16$1$1${'A' * 88}$a2V5", id="a-salt-longer-than-64-bytes"),
        pytest.param(f"scrypt$16$1$1${_SALT_16}${'A' * 600}", id="a-text-longer-than-the-ceiling"),
    ],
)
def test_a_stored_hash_with_a_field_outside_its_size_is_refused_without_being_computed(
    monkeypatch: pytest.MonkeyPatch, stored: str
) -> None:
    computed: list[object] = []
    monkeypatch.setattr(credentials.hashlib, "scrypt", lambda *args, **kwargs: computed.append((args, kwargs)))

    assert not verify_password(_PASSWORD, stored)
    assert computed == []


def test_the_hash_for_a_name_with_no_account_is_fixed_and_verifies_no_password() -> None:
    fixed = no_account_hash()

    assert no_account_hash() is fixed
    assert fixed.startswith("scrypt$32768$8$3$")
    assert not verify_password(_PASSWORD, fixed)
    assert not verify_password("", fixed)


@pytest.mark.parametrize(
    ("make", "prefix"),
    [(new_api_token, "ifs_pat_"), (new_session_token, "ifs_ses_")],
    ids=("api-token", "session-token"),
)
def test_a_token_value_has_its_prefix_and_32_random_bytes(make, prefix: str) -> None:
    first, second = make(), make()

    assert first.startswith(prefix)
    random_part = first.removeprefix(prefix)
    assert len(urlsafe_b64decode(random_part + "=")) == 32
    assert first != second


def test_the_random_part_of_a_token_comes_from_32_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[int] = []

    def token_urlsafe(nbytes: int) -> str:
        asked.append(nbytes)
        return "random"

    monkeypatch.setattr(credentials.secrets, "token_urlsafe", token_urlsafe)

    assert new_api_token() == "ifs_pat_random"
    assert new_session_token() == "ifs_ses_random"
    assert asked == [32, 32]


def test_the_digest_of_a_value_is_its_sha_256_in_hexadecimal() -> None:
    value = "ifs_pat_example"

    assert digest(value) == sha256(value.encode()).hexdigest()
    assert len(digest(value)) == 64


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("rotate ifs_pat_abc now", True),
        ("ifs_ses_abc", True),
        ("no token here", False),
        ("ifs_pa", False),
        ("", False),
    ],
)
def test_a_text_that_contains_a_token_prefix_is_recognized(text: str, *, expected: bool) -> None:
    assert contains_token_prefix(text) is expected


def _hold_places(gate: VerificationGate, count: int) -> tuple[threading.Event, list[threading.Thread]]:
    """Start ``count`` threads that each hold one place of the gate until the event is set."""
    release = threading.Event()
    holding = threading.Barrier(count + 1)

    def hold() -> None:
        with gate.place():
            holding.wait(timeout=10)
            release.wait(timeout=10)

    threads = [threading.Thread(target=hold) for _ in range(count)]
    for thread in threads:
        thread.start()
    holding.wait(timeout=10)
    return release, threads


def test_the_gate_refuses_a_third_concurrent_verification_after_its_wait() -> None:
    gate = VerificationGate(wait_seconds=0.05)
    stored = hash_password(_PASSWORD, **_FAST)
    release, threads = _hold_places(gate, 2)

    try:
        with pytest.raises(SignInBusyError) as refused:
            gate.verify(_PASSWORD, stored)
    finally:
        release.set()
        for thread in threads:
            thread.join(timeout=10)

    assert _PASSWORD not in str(refused.value)
    assert stored not in str(refused.value)
    # The places are free again, so the same verification now runs.
    assert gate.verify(_PASSWORD, stored)


def test_the_gate_lets_two_verifications_run_at_once() -> None:
    gate = VerificationGate(wait_seconds=0.05)
    stored = hash_password(_PASSWORD, **_FAST)
    release, threads = _hold_places(gate, 1)

    try:
        assert gate.verify(_PASSWORD, stored)
        assert not gate.verify("another password", stored)
    finally:
        release.set()
        for thread in threads:
            thread.join(timeout=10)


def test_the_gate_frees_its_place_when_a_verification_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    gate = VerificationGate(limit=1, wait_seconds=0.05)
    stored = hash_password(_PASSWORD, **_FAST)

    def fail(_password: str, _stored_hash: str) -> bool:
        raise MemoryError

    monkeypatch.setattr(credentials, "verify_password", fail)
    with pytest.raises(MemoryError):
        gate.verify(_PASSWORD, stored)
    monkeypatch.undo()

    assert gate.verify(_PASSWORD, stored)


def test_the_gate_checks_a_stored_hash_that_cannot_be_read_against_the_fixed_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A damaged account row takes the time of a check, as a name with no account does."""
    gate = VerificationGate(limit=1, wait_seconds=0.01)
    fixed = no_account_hash()
    checked: list[str] = []
    monkeypatch.setattr(credentials, "verify_password", lambda _password, stored: checked.append(stored) or True)

    assert not gate.verify(_PASSWORD, "scrypt$32768$8$3$not-base64!$a2V5")
    assert checked == [fixed]


def test_the_gate_of_the_process_lets_two_verifications_run_and_waits_five_seconds() -> None:
    assert credentials.VERIFICATION_LIMIT == 2
    assert credentials.VERIFICATION_WAIT_SECONDS == 5
    assert isinstance(credentials.PASSWORD_VERIFICATION_GATE, VerificationGate)


def test_the_gate_asks_admit_while_it_holds_a_place_and_checks_nothing_when_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = VerificationGate(limit=1, wait_seconds=0.01)
    stored = hash_password(_PASSWORD, **_FAST)
    checked: list[str] = []
    monkeypatch.setattr(credentials, "verify_password", lambda _password, stored: checked.append(stored) or True)
    places_free_during_admit: list[bool] = []

    def admit() -> bool:
        # The one place is held: a second verification would wait for it.
        with pytest.raises(SignInBusyError):
            gate.verify(_PASSWORD, "other")
        places_free_during_admit.append(False)
        return False

    with pytest.raises(VerificationNotAdmittedError) as refused:
        gate.verify(_PASSWORD, stored, admit=admit)

    assert places_free_during_admit == [False]
    assert checked == []
    assert _PASSWORD not in str(refused.value)
    assert gate.verify(_PASSWORD, stored, admit=lambda: True) is True
    assert checked == [stored]


def test_a_password_that_utf_8_cannot_encode_is_not_hashed_and_verifies_nothing() -> None:
    password = "correct-horse-\ud800-battery"  # noqa: S105 - a test value, not a secret.

    with pytest.raises(PasswordEncodingError) as refused:
        hash_password(password, **_FAST)

    assert password not in str(refused.value)
    assert refused.value.__cause__ is None
    assert refused.value.__context__ is None
    assert verify_password(password, hash_password(_PASSWORD, **_FAST)) is False
    assert credentials.is_encodable(password) is False
    assert credentials.is_encodable(_PASSWORD) is True


def test_a_readable_hash_is_told_from_a_damaged_one() -> None:
    assert credentials.is_readable_hash(hash_password(_PASSWORD, **_FAST)) is True
    assert credentials.is_readable_hash("scrypt$32768$8$3$not-base64!$a2V5") is False
