"""The failure cause of a stage that cannot get its stored credential values (spec 027, FR-019).

The worker's client raises ``CredentialUnavailableError`` with one of seven reasons. Each reason is
recorded as the failure type ``credential_unavailable`` with its own cause and next action. The
next action of a missing credential names the command that creates it and says that an
administrator runs it (critique P5). No text of the error reaches the record.
"""

from __future__ import annotations

import pytest

pytest.importorskip("prefect")

from infrahub_sync.client.models import FAILURE_TYPES
from infrahub_sync.service.credential_client import CredentialUnavailableError
from infrahub_sync.service.failure_cause import failure_class, failure_record
from infrahub_sync.service.settings import CredentialSettingsError

FAILURE_TYPE = "credential_unavailable"
NAME = "netbox-lab"


def _unavailable(reason: str) -> CredentialUnavailableError:
    if reason == "credential-missing":
        return CredentialUnavailableError("credential-missing", code="credential-not-found", block="source", name=NAME)
    if reason == "credential-field-missing":
        return CredentialUnavailableError(
            "credential-field-missing",
            code="credential-field-missing",
            block="source",
            name=NAME,
            fields=("password", "username"),
        )
    if reason == "credential-undecryptable":
        return CredentialUnavailableError(
            "credential-undecryptable", code="credential-undecryptable", block="destination", name=NAME
        )
    if reason == "credential-format-removed":
        return CredentialUnavailableError("credential-format-removed", code="credential-format-removed")
    if reason == "release-refused":
        return CredentialUnavailableError("release-refused", code="execution-not-in-progress")
    assert reason in {"api-unreachable", "worker-token-refused"}
    return CredentialUnavailableError(reason)  # ty: ignore[invalid-argument-type] - TODO: the assert narrows it


@pytest.mark.parametrize(
    ("reason", "cause", "next_action"),
    [
        ("api-unreachable", "could not reach the Sync API", "INFRAHUB_SYNC_API_URL"),
        ("worker-token-refused", "refused the worker token", "INFRAHUB_SYNC_WORKER_TOKEN"),
        ("credential-missing", f"stored credential {NAME} that the source block names does not exist", "create"),
        ("credential-field-missing", "does not hold the fields password, username", f"credentials set {NAME}"),
        ("credential-undecryptable", f"cannot decrypt the stored credential {NAME}", "INFRAHUB_SYNC_ENCRYPTION_KEY"),
        ("release-refused", "refused to release", "execution-not-in-progress"),
    ],
)
def test_each_reason_has_its_own_cause_and_next_action(reason: str, cause: str, next_action: str) -> None:
    record = failure_record("plan", _unavailable(reason))

    assert record["failure_type"] == FAILURE_TYPE
    assert cause in record["cause"]
    assert next_action in record["cause"] + record["next_action"]
    assert record["next_action"].endswith("then plan again.")
    # The credential is no system: the failure names neither the source nor the destination.
    assert record["system"] is None


@pytest.mark.parametrize("resumable", [False, True])
def test_a_version_in_the_removed_credential_format_says_to_register_a_new_version(*, resumable: bool) -> None:
    record = failure_record("apply", _unavailable("credential-format-removed"), resumable=resumable)

    assert record["failure_type"] == FAILURE_TYPE
    assert "removed" in record["cause"]
    assert "`credentials` block" in record["cause"]
    assert "Register a new version" in record["next_action"]
    assert "credential: <name>" in record["next_action"]
    # No run of this version can continue: the next action never says to plan again or to resume.
    assert "plan again" not in record["next_action"]
    assert "runs resume" not in record["next_action"]
    assert record["system"] is None


def test_a_missing_credential_names_the_command_and_the_administrator() -> None:
    record = failure_record("plan", _unavailable("credential-missing"))

    assert f"`infrahub-sync credentials create {NAME}`" in record["next_action"]
    assert "administrator" in record["next_action"]


def test_the_failure_is_found_under_the_error_that_wraps_it() -> None:
    wrapped = RuntimeError("the stage could not start")
    wrapped.__cause__ = _unavailable("credential-missing")

    assert failure_class(wrapped) == FAILURE_TYPE


def test_a_worker_without_its_variables_is_not_an_internal_error() -> None:
    refused = CredentialSettingsError("INFRAHUB_SYNC_API_URL must be set")

    record = failure_record("plan", refused)

    assert record["failure_type"] == FAILURE_TYPE
    assert "INFRAHUB_SYNC_API_URL" in record["cause"]
    assert "INFRAHUB_SYNC_WORKER_TOKEN" in record["cause"]


def test_a_resumable_run_continues_with_its_saved_plan() -> None:
    record = failure_record("apply", _unavailable("api-unreachable"), resumable=True)

    assert "infrahub-sync runs resume RUN_ID" in record["next_action"]
    assert "plan again" not in record["next_action"]


def test_the_failure_type_is_one_the_api_names() -> None:
    assert FAILURE_TYPE in FAILURE_TYPES
