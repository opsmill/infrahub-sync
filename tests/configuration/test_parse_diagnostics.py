"""Closed-contract tests for public configuration parse diagnostics."""

from __future__ import annotations

import os
import subprocess  # noqa: S404 - fixed interpreter runs an in-repository determinism probe.
import sys
import textwrap
from collections.abc import Iterator, Mapping
from typing import Any

import pytest
from pydantic import ValidationError

from infrahub_sync.configuration import (
    ConfigurationPackage,
    ConfigurationPackageParseError,
    parse_configuration_package,
)

_PREFIX = "configuration package is invalid at "
_REJECTED_CANARY = "rejected-secret-canary"
_DELETE = object()


def _package_data() -> dict[str, Any]:
    return {
        "format_version": 1,
        "configuration": {
            "name": "from-netbox",
            "source": {"name": "netbox", "settings": {}},
            "destination": {"name": "infrahub", "settings": {}},
            "order": [],
            "schema_mapping": [],
            "diffsync_flags": [],
            "incremental": None,
        },
        "package_metadata": {"adapter_api_version": 1},
        "credentials": {"valid-name": {"provider": "env", "identifier": "TOKEN"}},
    }


def _caught_failure(data: dict[str, Any]) -> ConfigurationPackageParseError:
    with pytest.raises(ConfigurationPackageParseError) as caught:
        parse_configuration_package(data)
    return caught.value


def _parse_failure(data: dict[str, Any]) -> str:
    return str(_caught_failure(data))


def _parse_with_errors(
    monkeypatch: pytest.MonkeyPatch,
    errors: object,
    *,
    data: dict[str, Any] | None = None,
) -> str:
    def injected_errors(
        _error: ValidationError,
        *,
        include_url: bool = True,
        include_context: bool = True,
        include_input: bool = True,
    ) -> object:
        del include_url, include_context, include_input
        return errors

    monkeypatch.setattr(ValidationError, "errors", injected_errors)
    parse_data = data if data is not None else _package_data()
    del parse_data["configuration"]["name"]
    return _parse_failure(parse_data)


def _set_path(data: dict[str, Any], path: tuple[str, ...], value: object) -> None:
    target = data
    for component in path[:-1]:
        target = target[component]
    if value is _DELETE:
        del target[path[-1]]
    else:
        target[path[-1]] = value


@pytest.mark.parametrize(
    ("path", "value", "expected"),
    [
        pytest.param(
            ("configuration", "name"), _DELETE, "/configuration/name: required field is missing", id="missing"
        ),
        pytest.param(("configuration", "name"), 7, "/configuration/name: wrong type", id="scalar-type"),
        pytest.param(("configuration", "order"), "Device", "/configuration/order: wrong type", id="container-type"),
        pytest.param(("configuration", "source"), "netbox", "/configuration/source: wrong type", id="model-type"),
        pytest.param(
            ("configuration", "source", "settings"),
            [],
            "/configuration/source/settings: wrong type",
            id="mapping-type",
        ),
        pytest.param(("format_version",), 2, "/format_version: unsupported value", id="literal"),
        pytest.param(
            ("native-extra",), _REJECTED_CANARY, "/native-extra: unsupported declared field", id="native-extra"
        ),
        pytest.param(
            ("configuration", "source", "custom-extra"),
            _REJECTED_CANARY,
            "/configuration/source/custom-extra: unsupported declared field",
            id="custom-extra",
        ),
        pytest.param(
            ("credentials", "valid-name", "provider"),
            "ENV!",
            "/credentials/valid-name/provider: does not match required pattern",
            id="pattern",
        ),
        pytest.param(
            ("credentials", "valid-name", "identifier"),
            "",
            "/credentials/valid-name/identifier: value is too short",
            id="short-string",
        ),
        pytest.param(
            ("credentials", "valid-name", "identifier"),
            _REJECTED_CANARY * 30,
            "/credentials/valid-name/identifier: value is too long",
            id="long-string",
        ),
        pytest.param(
            ("configuration", "incremental"),
            {"full_resync_every": "not-an-integer"},
            "/configuration/incremental/full_resync_every: wrong type",
            id="integer-conversion",
        ),
        pytest.param(
            ("configuration", "incremental"),
            {"full_resync_every": None},
            "/configuration/incremental/full_resync_every: wrong type",
            id="integer-type",
        ),
        pytest.param(
            ("configuration", "incremental"),
            {"full_resync_every": 1.5},
            "/configuration/incremental/full_resync_every: wrong type",
            id="integer-from-float",
        ),
        pytest.param(
            ("configuration", "incremental"),
            {"full_resync_every": "9" * 5000},
            "/configuration/incremental/full_resync_every: number is outside supported range",
            id="integer-magnitude",
        ),
    ],
)
def test_public_parse_maps_named_pydantic_families_without_values(
    path: tuple[str, ...], value: object, expected: str
) -> None:
    data = _package_data()
    _set_path(data, path, value)

    message = _parse_failure(data)

    assert message == _PREFIX + expected
    assert _REJECTED_CANARY not in message


def test_public_parse_reports_invalid_credential_name_with_bad_children() -> None:
    data = _package_data()
    data["credentials"] = {
        "bad/name": {"provider": "ENV!", "identifier": ""},
    }

    message = _parse_failure(data)

    assert message == _PREFIX + (
        "/credentials/bad~1name: invalid credential reference name; "
        "/credentials/bad~1name/identifier: value is too short; "
        "/credentials/bad~1name/provider: does not match required pattern"
    )


def test_direct_model_caller_context_cannot_disable_credential_key_validation() -> None:
    data = _package_data()
    data["credentials"] = {"bad/name": {"provider": "env", "identifier": "TOKEN"}}

    with pytest.raises(ValidationError):
        ConfigurationPackage.model_validate(
            data,
            context={"skip_credential_key_validation": True, "arbitrary-caller-value": _ExplodingObject()},
        )


def test_direct_model_reports_credential_key_and_child_errors_together() -> None:
    data = _package_data()
    data["credentials"] = {"bad/name": {"provider": "ENV!", "identifier": ""}}

    with pytest.raises(ValidationError) as caught:
        ConfigurationPackage.model_validate(data)

    failures = {
        (tuple(error["loc"]), error["type"])
        for error in caught.value.errors(include_input=False, include_context=False, include_url=False)
    }
    assert failures == {
        (("credentials", "bad/name", "[key]"), "string_pattern_mismatch"),
        (("credentials", "bad/name", "provider"), "string_pattern_mismatch"),
        (("credentials", "bad/name", "identifier"), "string_too_short"),
    }


@pytest.mark.parametrize("names", [("z/name", "a~name"), ("a~name", "z/name")], ids=["forward", "reverse"])
def test_public_parse_orders_independent_credential_name_failures(names: tuple[str, str]) -> None:
    data = _package_data()
    data["credentials"] = {name: {"provider": "env", "identifier": "TOKEN"} for name in names}

    assert _parse_failure(data) == _PREFIX + (
        "/credentials/a~0name: invalid credential reference name; "
        "/credentials/z~1name: invalid credential reference name"
    )


@pytest.mark.parametrize(
    ("name", "expected_location"),
    [
        pytest.param("é" * 256, "/credentials/" + "é" * 256, id="multibyte-bounded-item"),
        pytest.param("é" * 257, "/credentials", id="multibyte-oversized-container-location"),
    ],
)
def test_public_parse_bounds_invalid_credential_name_locations(name: str, expected_location: str) -> None:
    data = _package_data()
    data["credentials"] = {name: {"provider": "env", "identifier": _REJECTED_CANARY}}

    message = _parse_failure(data)

    assert message == f"{_PREFIX}{expected_location}: invalid credential reference name"
    assert _REJECTED_CANARY not in message


def test_public_parse_degrades_oversized_credential_name_and_bad_child_to_container() -> None:
    data = _package_data()
    data["credentials"] = {"é" * 257: {"provider": "ENV!", "identifier": _REJECTED_CANARY}}

    error = _caught_failure(data)

    assert str(error) == _PREFIX + (
        "/credentials: does not match required pattern; /credentials: invalid credential reference name"
    )
    assert _REJECTED_CANARY not in str(error)


def test_public_parse_encodes_diagnostic_delimiters_in_credential_names() -> None:
    data = _package_data()
    data["credentials"] = {"bad: forged; next": {"provider": "env", "identifier": _REJECTED_CANARY}}

    assert _parse_failure(data) == _PREFIX + (
        r"/credentials/bad\u003a forged\u003b next: invalid credential reference name"
    )


def test_public_parse_preserves_lone_surrogate_credential_key_diagnostic() -> None:
    data = _package_data()
    data["credentials"] = {"bad\ud800key": {"provider": "env", "identifier": _REJECTED_CANARY}}

    error = _caught_failure(data)

    assert str(error) == _PREFIX + r"/credentials/bad\\ud800key: invalid Unicode surrogate"
    assert "invalid credential reference name" not in str(error)
    assert _REJECTED_CANARY not in str(error)


def test_public_parse_rejects_hostile_root_credentials_key_without_callbacks() -> None:
    data = _package_data()
    credentials = data.pop("credentials")
    data[_ExplodingStr("credentials")] = credentials

    assert _parse_failure(data) == _PREFIX + "/: non-string mapping key"


class _ExplodingList(list[object]):  # noqa: FURB189 - hostile metadata must be rejected by exact type.
    def __iter__(self) -> Iterator[object]:
        raise AssertionError


class _ExplodingDict(dict[str, object]):  # noqa: FURB189 - hostile metadata must be rejected by exact type.
    def get(self, key: str, default: object = None) -> object:  # noqa: PLR6301 - hostile override probe.
        del key, default
        raise AssertionError


class _ExplodingTuple(tuple[object, ...]):
    __slots__ = ()

    def __iter__(self) -> Iterator[object]:
        raise AssertionError


class _ExplodingStr(str):  # noqa: FURB189 - hostile metadata must be rejected by exact type.
    __slots__ = ()
    __hash__ = str.__hash__

    def __str__(self) -> str:
        raise AssertionError

    def __eq__(self, other: object) -> bool:
        del other
        raise AssertionError


class _ExplodingInt(int):
    __hash__ = int.__hash__

    def __str__(self) -> str:
        raise AssertionError

    def __eq__(self, other: object) -> bool:
        del other
        raise AssertionError

    def __lt__(self, other: object) -> bool:
        del other
        raise AssertionError


class _ExplodingIterable:
    def __iter__(self) -> Iterator[object]:
        raise AssertionError


class _ExplodingObject:
    def __str__(self) -> str:
        raise AssertionError


class _ExplodingMapping(Mapping[str, object]):
    def __getitem__(self, key: str) -> object:
        del key
        raise AssertionError

    def __iter__(self) -> Iterator[str]:
        raise AssertionError

    def __len__(self) -> int:
        raise AssertionError


@pytest.mark.parametrize(
    "credentials",
    [
        pytest.param(_ExplodingDict(), id="dict-subclass"),
        pytest.param(_ExplodingMapping(), id="mapping-protocol"),
    ],
)
def test_public_parse_rejects_hostile_credential_containers_without_callbacks(credentials: object) -> None:
    data = _package_data()
    data["credentials"] = credentials

    assert _parse_failure(data) == _PREFIX + "/credentials: non-JSON value"


@pytest.mark.parametrize(
    "errors",
    [
        pytest.param([], id="empty"),
        pytest.param(_ExplodingList(), id="list-subclass"),
        pytest.param(_ExplodingIterable(), id="iterable-protocol"),
        pytest.param(
            [{"type": "missing", "loc": (index,)} for index in range(257)],
            id="too-many-records",
        ),
    ],
)
def test_public_parse_rejects_malformed_error_collections(monkeypatch: pytest.MonkeyPatch, errors: object) -> None:
    assert _parse_with_errors(monkeypatch, errors) == _PREFIX + "/: invalid value"


def test_public_parse_removes_rejected_values_from_exception_links() -> None:
    data = _package_data()
    data["credentials"]["valid-name"]["identifier"] = _REJECTED_CANARY * 30

    error = _caught_failure(data)
    rendered = (str(error), repr(error), repr(error.__cause__), repr(error.__context__))

    assert error.__cause__ is None
    assert error.__context__ is None
    assert all(_REJECTED_CANARY not in surface for surface in rendered)


def _malformed_core_record(case: str) -> object:  # noqa: PLR0911,PLR0912 - compact table fixture factory.
    if case == "record-subclass":
        return _ExplodingDict(type="missing", loc=("configuration", "name"))
    if case == "record-protocol":
        return _ExplodingMapping()
    if case == "record-key-subclass":
        return {"type": "missing", "loc": ("configuration", "name"), _ExplodingStr("ignored"): None}
    if case == "record-key-non-string":
        return {"type": "missing", "loc": ("configuration", "name"), 1: None}
    if case == "record-key-too-long":
        return {"type": "missing", "loc": ("configuration", "name"), "é" * 65: None}
    if case == "record-too-many-entries":
        return {"type": "missing", "loc": ("configuration", "name"), **{f"ignored-{i}": None for i in range(15)}}
    if case == "type-subclass":
        return {"type": _ExplodingStr("missing"), "loc": ("configuration", "name")}
    if case == "type-missing":
        return {"loc": ("configuration", "name")}
    if case == "type-integer":
        return {"type": 1, "loc": ("configuration", "name")}
    if case == "type-too-long":
        return {"type": "é" * 129, "loc": ("configuration", "name")}
    if case == "loc-list":
        return {"type": "missing", "loc": ["configuration", "name"]}
    if case == "loc-subclass":
        return {"type": "missing", "loc": _ExplodingTuple(("configuration", "name"))}
    if case == "loc-protocol":
        return {"type": "missing", "loc": _ExplodingIterable()}
    if case == "loc-too-deep":
        return {"type": "missing", "loc": ("x",) * 65}
    if case == "string-subclass":
        return {"type": "missing", "loc": (_ExplodingStr("configuration"),)}
    if case == "string-too-long":
        return {"type": "missing", "loc": ("x" * 257,)}
    if case == "integer-subclass":
        return {"type": "missing", "loc": (_ExplodingInt(1),)}
    if case == "negative-integer":
        return {"type": "missing", "loc": (-1,)}
    if case == "large-integer":
        return {"type": "missing", "loc": (2**63,)}
    raise AssertionError(case)


@pytest.mark.parametrize(
    "case",
    [
        "record-subclass",
        "record-protocol",
        "record-key-subclass",
        "record-key-non-string",
        "record-key-too-long",
        "record-too-many-entries",
        "type-subclass",
        "type-missing",
        "type-integer",
        "type-too-long",
        "loc-list",
        "loc-subclass",
        "loc-protocol",
        "loc-too-deep",
        "string-subclass",
        "string-too-long",
        "integer-subclass",
        "negative-integer",
        "large-integer",
    ],
)
def test_public_parse_rejects_malformed_error_record_core(monkeypatch: pytest.MonkeyPatch, case: str) -> None:
    assert _parse_with_errors(monkeypatch, [_malformed_core_record(case)]) == _PREFIX + "/: invalid value"


def test_public_parse_accepts_maximum_error_record_keys_and_ignores_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = {
        "type": "missing",
        "loc": ("configuration", "name"),
        "é" * 64: _ExplodingObject(),
        **{f"ignored-{index}": _ExplodingDict(secret=_REJECTED_CANARY) for index in range(13)},
    }

    message = _parse_with_errors(monkeypatch, [error])

    assert message == _PREFIX + "/configuration/name: required field is missing"
    assert _REJECTED_CANARY not in message


@pytest.mark.parametrize(
    "child",
    [
        pytest.param(_ExplodingStr("hostile"), id="string-subclass"),
        pytest.param("é" * 257, id="multibyte-string-too-long"),
        pytest.param(_ExplodingInt(1), id="integer-subclass"),
        pytest.param(True, id="boolean"),
        pytest.param(1.5, id="float"),
        pytest.param(_ExplodingObject(), id="arbitrary-object"),
        pytest.param(2**63, id="integer-too-large"),
    ],
)
def test_public_parse_falls_back_to_nearest_valid_location_prefix(
    monkeypatch: pytest.MonkeyPatch, child: object
) -> None:
    errors = [{"type": "future_error", "loc": ("configuration", child, "ignored")}]

    assert _parse_with_errors(monkeypatch, errors) == _PREFIX + "/configuration: invalid value"


def test_public_parse_accepts_maximum_location_integer(monkeypatch: pytest.MonkeyPatch) -> None:
    errors = [{"type": "future_error", "loc": (2**63 - 1,)}]

    assert _parse_with_errors(monkeypatch, errors) == _PREFIX + f"/{2**63 - 1}: invalid value"


def test_public_parse_encodes_diagnostic_delimiters_in_ordinary_locations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    errors = [{"type": "future_error", "loc": ("bad: forged; next",)}]

    assert _parse_with_errors(monkeypatch, errors) == _PREFIX + (r"/bad\u003a forged\u003b next: invalid value")


@pytest.mark.parametrize(
    ("error_type", "location", "reason"),
    [
        pytest.param("invalid_json_value", ("configuration",), "invalid JSON value", id="json"),
        pytest.param("invalid_unicode_surrogate", ("configuration",), "invalid Unicode surrogate", id="unicode"),
        pytest.param("unsupported_declared_fields", ("configuration",), "unsupported declared field", id="unsupported"),
        pytest.param("invalid_diffsync_flag_name", ("configuration",), "invalid diffsync flag name", id="diffsync"),
    ],
)
@pytest.mark.parametrize(
    "context",
    [
        pytest.param(None, id="absent"),
        pytest.param({}, id="empty"),
        pytest.param([], id="wrong-container"),
        pytest.param(_ExplodingDict(), id="dict-subclass"),
        pytest.param(_ExplodingMapping(), id="mapping-protocol"),
    ],
)
def test_public_parse_requires_exact_context_for_every_custom_family(
    monkeypatch: pytest.MonkeyPatch,
    error_type: str,
    location: tuple[str, ...],
    reason: str,
    context: object,
) -> None:
    errors = [{"type": error_type, "loc": location, "ctx": context}]

    assert _parse_with_errors(monkeypatch, errors) == f"{_PREFIX}/{'/'.join(location)}: {reason}"


_CUSTOM_CONTEXT_FAMILIES = [
    pytest.param(
        (
            "invalid_json_value",
            {"pointer": "/trusted", "reason": "non-JSON value"},
            "/trusted: non-JSON value",
            "invalid JSON value",
        ),
        id="json",
    ),
    pytest.param(
        (
            "invalid_unicode_surrogate",
            {"pointer": "/trusted"},
            "/trusted: invalid Unicode surrogate",
            "invalid Unicode surrogate",
        ),
        id="unicode",
    ),
    pytest.param(
        (
            "unsupported_declared_fields",
            {"pointer": "/trusted", "field_names": ("field",)},
            "/trusted/field: unsupported declared field",
            "unsupported declared field",
        ),
        id="unsupported",
    ),
    pytest.param(
        (
            "invalid_diffsync_flag_name",
            {"index": 0, "reason": "unknown diffsync flag name"},
            "/configuration/source/0: unknown diffsync flag name",
            "invalid diffsync flag name",
        ),
        id="diffsync",
    ),
]


def _context_with_invalid_key_shape(base: dict[str, object], case: str) -> dict[object, object]:
    items: list[tuple[object, object]] = list(base.items())
    context = dict(items)
    if case == "key-subclass":
        context[_ExplodingStr("ignored-hostile")] = _ExplodingObject()
    elif case == "non-string-key":
        context[1] = _ExplodingObject()
    elif case == "key-too-long":
        context["é" * 65] = _ExplodingObject()
    elif case == "too-many-entries":
        index = 0
        while len(context) < 17:
            context[f"ignored-{index}"] = _ExplodingObject()
            index += 1
    else:
        raise AssertionError(case)
    return context


@pytest.mark.parametrize(
    "family",
    _CUSTOM_CONTEXT_FAMILIES,
)
@pytest.mark.parametrize(
    "case",
    ["key-subclass", "non-string-key", "key-too-long", "too-many-entries"],
)
def test_public_parse_validates_all_custom_context_keys_before_lookup(
    monkeypatch: pytest.MonkeyPatch,
    family: tuple[str, dict[str, object], str, str],
    case: str,
) -> None:
    error_type, base_context, _valid_result, coarse_reason = family
    errors = [
        {
            "type": error_type,
            "loc": ("configuration", "source"),
            "ctx": _context_with_invalid_key_shape(base_context, case),
        }
    ]

    message = _parse_with_errors(monkeypatch, errors)

    assert message == f"{_PREFIX}/configuration/source: {coarse_reason}"
    assert _REJECTED_CANARY not in message


@pytest.mark.parametrize(
    "family",
    _CUSTOM_CONTEXT_FAMILIES,
)
def test_public_parse_accepts_maximum_custom_context_keys_and_ignores_values(
    monkeypatch: pytest.MonkeyPatch,
    family: tuple[str, dict[str, object], str, str],
) -> None:
    error_type, base_context, valid_result, _coarse_reason = family
    context: dict[str, object] = dict(base_context)
    context["é" * 64] = _ExplodingDict(secret=_REJECTED_CANARY)
    index = 0
    while len(context) < 16:
        context[f"ignored-{index}"] = _ExplodingObject()
        index += 1
    errors = [{"type": error_type, "loc": ("configuration", "source"), "ctx": context}]

    message = _parse_with_errors(monkeypatch, errors)

    assert message == _PREFIX + valid_result
    assert _REJECTED_CANARY not in message


@pytest.mark.parametrize(
    "family",
    [
        pytest.param(
            ("unsupported_declared_fields", ("configuration",), "/configuration", "unsupported declared field"),
            id="unsupported",
        ),
    ],
)
@pytest.mark.parametrize(
    "field_names",
    [
        pytest.param(_DELETE, id="missing"),
        pytest.param(None, id="null"),
        pytest.param((), id="empty"),
        pytest.param([], id="wrong-container"),
        pytest.param(_ExplodingTuple(("bad",)), id="tuple-subclass"),
        pytest.param(_ExplodingIterable(), id="iterable-protocol"),
        pytest.param(("bad",) * 129, id="too-many"),
        pytest.param((1,), id="wrong-member"),
        pytest.param((_ExplodingStr("bad"),), id="member-subclass"),
        pytest.param(("é" * 257,), id="multibyte-member-too-long"),
    ],
)
def test_public_parse_rejects_malformed_custom_field_collections(
    monkeypatch: pytest.MonkeyPatch,
    family: tuple[str, tuple[str, ...], str | None, str],
    field_names: object,
) -> None:
    error_type, location, pointer, reason = family
    context: dict[str, object] = {}
    if pointer is not None:
        context["pointer"] = pointer
    if field_names is not _DELETE:
        context["field_names"] = field_names
    errors = [{"type": error_type, "loc": location, "ctx": context}]

    assert _parse_with_errors(monkeypatch, errors) == f"{_PREFIX}/{'/'.join(location)}: {reason}"


@pytest.mark.parametrize(
    "reason",
    [
        pytest.param(_DELETE, id="missing"),
        pytest.param(None, id="null"),
        pytest.param(1, id="wrong-type"),
        pytest.param("unknown-reason", id="unknown"),
        pytest.param("é" * 257, id="multibyte-too-long"),
        pytest.param(_ExplodingStr("non-JSON value"), id="string-subclass"),
    ],
)
def test_public_parse_rejects_malformed_json_reason(monkeypatch: pytest.MonkeyPatch, reason: object) -> None:
    context: dict[str, object] = {"pointer": "/configuration"}
    if reason is not _DELETE:
        context["reason"] = reason
    errors = [{"type": "invalid_json_value", "loc": ("configuration",), "ctx": context}]

    assert _parse_with_errors(monkeypatch, errors) == _PREFIX + "/configuration: invalid JSON value"


def _malformed_diffsync_context(case: str) -> dict[str, object]:  # noqa: PLR0912 - table fixture factory.
    context: dict[str, object] = {"index": 0, "reason": "unknown diffsync flag name"}
    if case == "missing-reason":
        del context["reason"]
    elif case == "empty-reason":
        context["reason"] = ""
    elif case == "wrong-reason":
        context["reason"] = 1
    elif case == "long-reason":
        context["reason"] = "é" * 257
    elif case == "hostile-reason":
        context["reason"] = _ExplodingStr("unknown diffsync flag name")
    elif case == "missing-index":
        del context["index"]
    elif case == "boolean-index":
        context["index"] = True
    elif case == "float-index":
        context["index"] = 1.5
    elif case == "object-index":
        context["index"] = _ExplodingObject()
    elif case == "negative-index":
        context["index"] = -1
    elif case == "large-index":
        context["index"] = 2**63
    elif case == "hostile-index":
        context["index"] = _ExplodingInt(0)
    else:
        raise AssertionError(case)
    return context


@pytest.mark.parametrize(
    "case",
    [
        "missing-reason",
        "empty-reason",
        "wrong-reason",
        "long-reason",
        "hostile-reason",
        "missing-index",
        "boolean-index",
        "float-index",
        "object-index",
        "negative-index",
        "large-index",
        "hostile-index",
    ],
)
def test_public_parse_rejects_malformed_diffsync_context(monkeypatch: pytest.MonkeyPatch, case: str) -> None:
    errors = [
        {
            "type": "invalid_diffsync_flag_name",
            "loc": ("configuration", "diffsync_flags"),
            "ctx": _malformed_diffsync_context(case),
        }
    ]

    assert _parse_with_errors(monkeypatch, errors) == _PREFIX + (
        "/configuration/diffsync_flags: invalid diffsync flag name"
    )


@pytest.mark.parametrize(
    "pointer",
    [
        pytest.param(_ExplodingStr("/hostile"), id="string-subclass"),
        pytest.param("/" + "é" * 4096, id="multibyte-too-long"),
        pytest.param("relative/path", id="relative"),
        pytest.param("~", id="bare-tilde"),
        pytest.param("/bad~", id="trailing-tilde"),
        pytest.param("/bad~2escape", id="bad-rfc6901-escape"),
        pytest.param("/valid~0/bad~2", id="valid-then-invalid-escape"),
        pytest.param("/bad~2/valid~1", id="invalid-then-valid-escape"),
        pytest.param("/bad\n" + _REJECTED_CANARY, id="control"),
        pytest.param("/bad\x7f" + _REJECTED_CANARY, id="del-control"),
        pytest.param("/bad\x85" + _REJECTED_CANARY, id="c1-control"),
        pytest.param("/bad\u2028" + _REJECTED_CANARY, id="line-separator"),
        pytest.param("/bad\u2029" + _REJECTED_CANARY, id="paragraph-separator"),
        pytest.param("/bad\u200b" + _REJECTED_CANARY, id="zero-width-format"),
        pytest.param("/bad\u202e" + _REJECTED_CANARY, id="bidi-override"),
        pytest.param("/bad\u2066" + _REJECTED_CANARY, id="bidi-isolate-left-to-right"),
        pytest.param("/bad\u2067" + _REJECTED_CANARY, id="bidi-isolate-right-to-left"),
        pytest.param("/bad\u2068" + _REJECTED_CANARY, id="bidi-isolate-first-strong"),
        pytest.param("/bad\u2069" + _REJECTED_CANARY, id="bidi-isolate-pop"),
        pytest.param("/bad\ud800" + _REJECTED_CANARY, id="surrogate"),
    ],
)
@pytest.mark.parametrize(
    ("error_type", "extra_context", "safe_reason"),
    [
        pytest.param("invalid_json_value", {"reason": "non-JSON value"}, "invalid JSON value", id="json"),
        pytest.param("invalid_unicode_surrogate", {}, "invalid Unicode surrogate", id="unicode"),
        pytest.param(
            "unsupported_declared_fields",
            {"field_names": ("bad",)},
            "unsupported declared field",
            id="unsupported",
        ),
    ],
)
def test_public_parse_rejects_malformed_or_invisible_context_pointers(
    monkeypatch: pytest.MonkeyPatch,
    pointer: object,
    error_type: str,
    extra_context: dict[str, object],
    safe_reason: str,
) -> None:
    context = {"pointer": pointer, **extra_context}
    errors = [
        {
            "type": error_type,
            "loc": ("configuration", "source"),
            "ctx": context,
        }
    ]

    message = _parse_with_errors(monkeypatch, errors)

    assert message == f"{_PREFIX}/configuration/source: {safe_reason}"
    assert _REJECTED_CANARY not in message
    assert len(message.splitlines()) == 1
    assert all(character.isprintable() for character in message)


def test_public_parse_accepts_and_encodes_safe_context_pointer(monkeypatch: pytest.MonkeyPatch) -> None:
    errors = [
        {
            "type": "invalid_json_value",
            "loc": ("configuration",),
            "ctx": {
                "pointer": "/valid~0tilde/valid~1slash/bad: forged; next",
                "reason": "non-JSON value",
            },
        }
    ]

    assert _parse_with_errors(monkeypatch, errors) == _PREFIX + (
        r"/valid~0tilde/valid~1slash/bad\u003a forged\u003b next: non-JSON value"
    )


def test_public_parse_keeps_context_pointer_delimiter_encoding_collision_free(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    errors = [
        {
            "type": "invalid_json_value",
            "loc": ("configuration",),
            "ctx": {"pointer": "/bad:field", "reason": "non-JSON value"},
        },
        {
            "type": "invalid_json_value",
            "loc": ("configuration",),
            "ctx": {"pointer": r"/bad\u003afield", "reason": "non-JSON value"},
        },
    ]

    assert _parse_with_errors(monkeypatch, errors) == _PREFIX + (
        r"/bad\\u003afield: non-JSON value; /bad\u003afield: non-JSON value"
    )


@pytest.mark.parametrize(
    "character",
    [
        pytest.param("\u00a0", id="non-breaking-space"),
        pytest.param("\u0378", id="unassigned-scalar"),
    ],
)
def test_public_parse_rejects_non_printable_context_pointer_scalars(
    monkeypatch: pytest.MonkeyPatch,
    character: str,
) -> None:
    errors = [
        {
            "type": "invalid_json_value",
            "loc": ("configuration", "source"),
            "ctx": {"pointer": f"/bad{character}field", "reason": "non-JSON value"},
        }
    ]

    message = _parse_with_errors(monkeypatch, errors)

    assert message == _PREFIX + "/configuration/source: invalid JSON value"
    assert all(rendered.isprintable() for rendered in message)


def test_public_parse_keeps_escaped_locations_collision_free(monkeypatch: pytest.MonkeyPatch) -> None:
    errors = [
        {"type": "future_error", "loc": ("bad\n\u202e\ud800~/",)},
        {"type": "future_error", "loc": (r"bad\n\u202e\ud800~/",)},
    ]

    message = _parse_with_errors(monkeypatch, errors)

    assert message == _PREFIX + (
        r"/bad\\n\\u202e\\ud800~0~1: invalid value; "
        r"/bad\n\u202e\ud800~0~1: invalid value"
    )
    assert len(message.splitlines()) == 1
    assert all(character.isprintable() for character in message)


def test_public_parse_keeps_delimiter_encoding_collision_free(monkeypatch: pytest.MonkeyPatch) -> None:
    errors = [
        {"type": "future_error", "loc": ("bad: forged; next",)},
        {"type": "future_error", "loc": (r"bad\u003a forged\u003b next",)},
    ]

    assert _parse_with_errors(monkeypatch, errors) == _PREFIX + (
        r"/bad\\u003a forged\\u003b next: invalid value; "
        r"/bad\u003a forged\u003b next: invalid value"
    )


def test_public_parse_ignores_untrusted_metadata_and_keeps_mixed_records(monkeypatch: pytest.MonkeyPatch) -> None:
    errors = [
        {
            "type": "missing",
            "loc": ("configuration", "name"),
            "msg": _ExplodingStr(_REJECTED_CANARY),
            "input": _ExplodingDict(secret=_REJECTED_CANARY),
            "url": _ExplodingStr(_REJECTED_CANARY),
            "ctx": {"ignored": _ExplodingIterable()},
        },
        _ExplodingDict(type="missing", loc=("credentials",)),
    ]

    message = _parse_with_errors(monkeypatch, errors)

    assert message == _PREFIX + "/: invalid value; /configuration/name: required field is missing"
    assert _REJECTED_CANARY not in message


def test_public_parse_ignores_all_metadata_on_unknown_error_type(monkeypatch: pytest.MonkeyPatch) -> None:
    errors = [
        {
            "type": "future_error",
            "loc": ("configuration",),
            "ctx": _ExplodingDict(secret=_REJECTED_CANARY),
            "msg": _ExplodingStr(_REJECTED_CANARY),
            "input": _ExplodingIterable(),
            "url": _ExplodingStr(_REJECTED_CANARY),
        }
    ]

    message = _parse_with_errors(monkeypatch, errors)

    assert message == _PREFIX + "/configuration: invalid value"
    assert _REJECTED_CANARY not in message


@pytest.mark.parametrize("order", ["forward", "reverse"])
def test_public_parse_sorts_and_deduplicates_decoded_records(monkeypatch: pytest.MonkeyPatch, order: str) -> None:
    errors = [
        {"type": "string_type", "loc": ("configuration", "source")},
        {"type": "missing", "loc": ("configuration", "name")},
        {"type": "missing", "loc": ("configuration", "name")},
    ]
    if order == "reverse":
        errors.reverse()

    assert _parse_with_errors(monkeypatch, errors) == _PREFIX + (
        "/configuration/name: required field is missing; /configuration/source: wrong type"
    )


@pytest.mark.parametrize("hash_seed", ["1", "37", "101"])
def test_public_parse_is_deterministic_across_hash_seeds(hash_seed: str) -> None:
    script = textwrap.dedent(
        f"""
        from pydantic import ValidationError
        from infrahub_sync.configuration import ConfigurationPackageParseError, parse_configuration_package

        data = {_package_data()!r}
        del data["configuration"]["name"]
        fields = {{"source", "name"}}
        records = [
            {{"type": "missing" if field == "name" else "string_type", "loc": ("configuration", field)}}
            for field in fields
        ]
        ValidationError.errors = lambda self, **kwargs: records
        try:
            parse_configuration_package(data)
        except ConfigurationPackageParseError as error:
            print(error)
        """
    )
    environment = os.environ.copy()
    environment["PYTHONHASHSEED"] = hash_seed

    result = subprocess.run(  # noqa: S603 - fixed current interpreter and static script.
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        env=environment,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == _PREFIX + (
        "/configuration/name: required field is missing; /configuration/source: wrong type"
    )


def test_public_parse_accepts_distinct_final_error_record_at_256(monkeypatch: pytest.MonkeyPatch) -> None:
    errors = [{"type": "future_error", "loc": ("record", index)} for index in range(256)]

    details = _parse_with_errors(monkeypatch, errors).removeprefix(_PREFIX).split("; ")

    assert len(details) == 256
    assert set(details) == {f"/record/{index}: invalid value" for index in range(256)}


def test_public_parse_accepts_distinct_final_custom_field_at_128(monkeypatch: pytest.MonkeyPatch) -> None:
    names = tuple(f"field-{index:03}" for index in range(128))
    errors = [
        {
            "type": "unsupported_declared_fields",
            "loc": ("configuration",),
            "ctx": {"pointer": "/configuration", "field_names": names},
        }
    ]

    details = _parse_with_errors(monkeypatch, errors).removeprefix(_PREFIX).split("; ")

    assert len(details) == 128
    assert details[-1] == "/configuration/field-127: unsupported declared field"


@pytest.mark.parametrize("expanded_count", [256, 257], ids=["exact-maximum", "overflow"])
def test_public_parse_bounds_total_findings_after_custom_field_expansion(
    monkeypatch: pytest.MonkeyPatch,
    expanded_count: int,
) -> None:
    errors = [
        {
            "type": "unsupported_declared_fields",
            "loc": ("configuration",),
            "ctx": {
                "pointer": f"/expanded/{group}",
                "field_names": tuple(f"field-{index:03}" for index in range(128)),
            },
        }
        for group in ("a", "b")
    ]
    if expanded_count == 257:
        errors.append(
            {
                "type": "unsupported_declared_fields",
                "loc": ("configuration",),
                "ctx": {"pointer": "/expanded/c", "field_names": ("final-field",)},
            }
        )

    message = _parse_with_errors(monkeypatch, errors)

    if expanded_count == 257:
        assert message == _PREFIX + "/: invalid value"
    else:
        details = message.removeprefix(_PREFIX).split("; ")
        assert len(details) == 256
        assert details[-1] == "/expanded/b/field-127: unsupported declared field"


@pytest.mark.parametrize("name_count", [256, 257], ids=["exact-maximum", "overflow"])
def test_public_parse_bounds_native_credential_key_findings(name_count: int) -> None:
    data = _package_data()
    names = tuple(f"bad/name-{index:03}" for index in range(name_count))
    data["credentials"] = {name: {"provider": "env", "identifier": "TOKEN"} for name in names}

    message = _parse_failure(data)

    if name_count == 257:
        assert message == _PREFIX + "/: invalid value"
    else:
        details = message.removeprefix(_PREFIX).split("; ")
        assert len(details) == 256
        assert set(details) == {
            f"/credentials/bad~1name-{index:03}: invalid credential reference name" for index in range(256)
        }


def test_public_parse_checks_natural_error_count_before_materializing_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = _package_data()
    names = tuple(f"bad/name-{index:03}" for index in range(257))
    data["credentials"] = {name: {"provider": "env", "identifier": "TOKEN"} for name in names}
    real_errors = ValidationError.errors
    errors_calls = 0

    def observed_errors(
        error: ValidationError,
        *,
        include_url: bool = True,
        include_context: bool = True,
        include_input: bool = True,
    ) -> object:
        nonlocal errors_calls
        errors_calls += 1
        return real_errors(
            error,
            include_url=include_url,
            include_context=include_context,
            include_input=include_input,
        )

    monkeypatch.setattr(ValidationError, "errors", observed_errors)

    assert _parse_failure(data) == _PREFIX + "/: invalid value"
    assert errors_calls == 0


@pytest.mark.parametrize(
    ("errors", "expected"),
    [
        pytest.param(
            [
                {
                    "type": "invalid_json_value",
                    "loc": ("configuration",),
                    "ctx": {"pointer": "/configuration", "reason": "non-JSON value"},
                }
            ]
            * 256,
            "/configuration: non-JSON value",
            id="collection-maximum-deduplicated",
        ),
        pytest.param(
            [{"type": "é" * 128, "loc": ("é" * 256,) + (0,) * 63}],
            "/" + "é" * 256 + "/0" * 63 + ": invalid value",
            id="core-maxima",
        ),
        pytest.param(
            [
                {
                    "type": "invalid_json_value",
                    "loc": ("configuration",),
                    "ctx": {"pointer": "/" + "é" * 4095, "reason": "non-JSON value"},
                }
            ],
            "/" + "é" * 4095 + ": non-JSON value",
            id="multibyte-pointer-maximum",
        ),
        pytest.param(
            [
                {
                    "type": "unsupported_declared_fields",
                    "loc": ("configuration",),
                    "ctx": {"pointer": "/configuration", "field_names": ("é" * 256,)},
                }
            ],
            "/configuration/" + "é" * 256 + ": unsupported declared field",
            id="multibyte-member-maximum",
        ),
        pytest.param(
            [
                {
                    "type": "invalid_diffsync_flag_name",
                    "loc": ("configuration",),
                    "ctx": {"index": 2**63 - 1, "reason": "unknown diffsync flag name"},
                }
            ],
            f"/configuration/{2**63 - 1}: unknown diffsync flag name",
            id="integer-maximum",
        ),
    ],
)
def test_public_parse_accepts_exact_metadata_resource_boundaries(
    monkeypatch: pytest.MonkeyPatch, errors: list[dict[str, object]], expected: str
) -> None:
    assert _parse_with_errors(monkeypatch, errors) == _PREFIX + expected
