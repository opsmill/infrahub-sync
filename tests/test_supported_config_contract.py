"""The supported configuration contract of `infrahub_sync`'s model layer.

Two admission behaviors that the rest of the suite only reaches indirectly:
`SyncConfig` coercing declared `diffsync_flags` into `DiffSyncFlags` members,
and `DiffSyncModelMixin.is_list` reading a field's default off a Pydantic-v2
model. The configuration-package boundary in `tests/configuration/` covers the
non-list refusal and the redacted package-level diagnostics; these pin the
model layer those tests call legacy behavior.
"""

from __future__ import annotations

import pydantic
import pytest
from diffsync import DiffSyncModel
from diffsync.enum import DiffSyncFlags

from infrahub_sync import DiffSyncModelMixin, SyncAdapter, SyncConfig


def _config(flags: list[str | DiffSyncFlags]) -> SyncConfig:
    return SyncConfig(
        name="contract-under-test",
        source=SyncAdapter(name="source"),
        destination=SyncAdapter(name="destination"),
        diffsync_flags=flags,
    )


# --- SyncConfig admits flags as DiffSyncFlags members -------------------------


@pytest.mark.parametrize(
    "name",
    ["SKIP_UNMATCHED_DST", "SKIP_UNMATCHED_SRC", "CONTINUE_ON_FAILURE", "NONE"],
)
def test_declared_flag_name_is_admitted_as_its_enum_member(name: str) -> None:
    admitted = _config([name]).diffsync_flags

    assert admitted is not None
    assert admitted == [DiffSyncFlags[name]]
    # `DiffSyncFlags` is an IntFlag, so equality alone would also accept the raw int.
    assert all(isinstance(flag, DiffSyncFlags) for flag in admitted)


def test_declared_enum_member_is_admitted_unchanged() -> None:
    assert _config([DiffSyncFlags.CONTINUE_ON_FAILURE]).diffsync_flags == [DiffSyncFlags.CONTINUE_ON_FAILURE]


def test_names_and_members_are_admitted_together_in_declared_order() -> None:
    admitted = _config(["SKIP_UNMATCHED_SRC", DiffSyncFlags.CONTINUE_ON_FAILURE]).diffsync_flags

    assert admitted == [DiffSyncFlags.SKIP_UNMATCHED_SRC, DiffSyncFlags.CONTINUE_ON_FAILURE]


@pytest.mark.parametrize("name", ["NOPE", "skip_unmatched_dst"], ids=["unknown", "wrong-case"])
def test_unknown_flag_name_is_refused_as_a_validation_error(name: str) -> None:
    with pytest.raises(pydantic.ValidationError) as caught:
        _config([name])

    errors = caught.value.errors()
    assert [error["type"] for error in errors] == ["value_error"]
    assert [error["loc"] for error in errors] == [("diffsync_flags",)]
    assert "Invalid DiffSyncFlags value" in errors[0]["msg"]


# --- DiffSyncModelMixin.is_list reads the declared default --------------------


class _Model(DiffSyncModelMixin, DiffSyncModel):
    """A model shaped like a generated one, plus the fields that separate
    reading a default from reading an annotation or a default factory."""

    _modelname = "model"
    _identifiers = ("name",)

    name: str
    tags: list[str] | None = []  # noqa: RUF012 - `is_list` reads this default
    label: str | None = None
    unset_peers: list[str] | None = None
    built_peers: list[str] = pydantic.Field(default_factory=list)


def test_is_list_is_true_for_a_list_default() -> None:
    assert _Model.is_list(name="tags") is True


@pytest.mark.parametrize(
    "field",
    ["label", "name", "unset_peers", "built_peers"],
    ids=["scalar-default", "no-default", "list-annotation-without-list-default", "default-factory"],
)
def test_is_list_is_false_without_a_list_default(field: str) -> None:
    assert _Model.is_list(name=field) is False


def test_is_list_refuses_a_field_the_model_does_not_declare() -> None:
    with pytest.raises(ValueError, match="Unable to find the field ghost"):
        _Model.is_list(name="ghost")
