"""Compatibility metadata boundary tests."""

from importlib.metadata import PackageNotFoundError

import pytest

from infrahub_sync.service import compatibility


@pytest.mark.parametrize(
    "failure",
    [PackageNotFoundError("infrahub-sync"), ValueError("corrupt metadata")],
)
def test_installed_server_version_translates_metadata_failures_without_cause(
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
) -> None:
    def fail_version(_distribution_name: str) -> str:
        raise failure

    monkeypatch.setattr(compatibility, "version", fail_version)

    with pytest.raises(RuntimeError, match=r"^service package metadata is unavailable$") as caught:
        compatibility.installed_server_version()

    assert caught.value.__cause__ is None


@pytest.mark.parametrize(
    "metadata_value",
    [
        None,
        "",
        " ",
        "not-a-version",
        "1..0",
        "1.0+",
        "1.0+local..part",
        "1!1!2",
    ],
)
def test_installed_server_version_rejects_invalid_metadata_values(
    monkeypatch: pytest.MonkeyPatch,
    metadata_value: object,
) -> None:
    monkeypatch.setattr(compatibility, "version", lambda _distribution_name: metadata_value)

    with pytest.raises(RuntimeError, match=r"^service package metadata is unavailable$") as caught:
        compatibility.installed_server_version()

    assert caught.value.__cause__ is None


@pytest.mark.parametrize(
    "metadata_value",
    [
        "0.0.0",
        "2.0",
        "02.0.1",
        "2.0.1.dev1",
        "2.0.1rc1",
        "2.0.1.post1",
        "2.0.1+linux_x86",
        "1!2.0.1",
        "v02.0.1-rc.1+LOCAL_7",
    ],
)
def test_installed_server_version_accepts_builtin_pep440_versions_without_normalizing(
    monkeypatch: pytest.MonkeyPatch,
    metadata_value: str,
) -> None:
    monkeypatch.setattr(compatibility, "version", lambda _distribution_name: metadata_value)

    assert compatibility.installed_server_version() == metadata_value


# The review document gained two optional fields, `lineage` and `last_changes` (research.md R-4).
_ADDITIVE_PLAN_FIELDS = ("lineage", "last_changes")


def test_the_additive_plan_review_fields_keep_the_declared_api_version() -> None:
    """The service still declares the one API version the client accepts, so no client is refused."""
    assert compatibility.API_VERSIONS == ("v3-unstable",)
    assert compatibility.API_STABILITY == "unstable"


def test_a_client_and_a_service_on_either_side_of_the_additive_plan_fields_read_each_other() -> None:
    """A newer client reads an older review; an older client reads a newer one.

    The older client stands for a review model released before the two fields, with the same
    configuration. It accepts the newer document because the review models allow unknown
    fields; the newer client accepts an older document because both fields default to `None`.
    """
    from pydantic import BaseModel

    from infrahub_sync.client import PlanResource

    class OlderPlanResource(BaseModel):
        model_config = PlanResource.model_config

        run_id: str
        checksum: str

    for name in _ADDITIVE_PLAN_FIELDS:
        assert not PlanResource.model_fields[name].is_required()
        assert PlanResource.model_fields[name].default is None
    older_document = {
        "run_id": "run-1",
        "checksum": "a" * 64,
        "checksum_ok": True,
        "verification_notes": [],
        "summary": {
            "by_action": {},
            "by_kind": {},
            "total": 0,
            "delete_operations_computed": True,
            "deletes_not_executed": 0,
        },
        "operations": [],
    }
    newer_document = older_document | {
        "lineage": {
            "import_account": {"name": "netbox", "id": "account-1"},
            "source": {"kind": "CoreAccount", "name": "netbox"},
            "owner": None,
            "protect": False,
        },
        "last_changes": [
            {
                "operation_id": "op_0123456789abcdef",
                "field": "description",
                "field_type": "attribute",
                "updated_by": None,
                "updated_at": None,
            }
        ],
    }

    assert PlanResource.model_validate(older_document).last_changes is None
    assert PlanResource.model_validate(older_document).lineage is None
    assert OlderPlanResource.model_validate(newer_document).run_id == "run-1"
