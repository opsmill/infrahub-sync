"""Behavioral contract for the shared ``configs`` application service.

The service is the layer both interfaces call. What is asserted here is therefore what the
CLI and the Python API are each forbidden to re-decide: which package is persisted, which
error family a failure belongs to, and the order findings arrive in.
"""

from __future__ import annotations

import inspect
import json
import sqlite3
import sys
from collections.abc import Iterator, Mapping
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest
from pydantic import ValidationError

from infrahub_sync.configuration import BUILTIN_ADAPTER_CAPABILITIES, ConfigurationPackage, ValidationFinding
from infrahub_sync.configuration.models import safe_pointer_component
from infrahub_sync.configuration.validation import collect_findings
from infrahub_sync.execution import MIN_SECRET_LENGTH, REDACTED, collect_secret_values
from infrahub_sync.product_store import configs as configs_service
from infrahub_sync.product_store import local_product_projection
from infrahub_sync.product_store import store as product_store_store
from tests.configuration.validation_packages import package, package_data

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


def _store(tmp_path: Path) -> str:
    root = tmp_path / "product-cache"
    root.mkdir()
    return str(root)


def _registered_configuration_count(root: Path) -> int:
    """Count registry rows directly, so 'persisted nothing' is read off the durable store."""
    database = root / "product-records.sqlite3"
    if not database.exists():
        return 0
    connection = sqlite3.connect(database)
    try:
        rows = connection.execute(
            "SELECT count(*) FROM sqlite_master WHERE type = 'table' AND name = 'configurations'"
        ).fetchone()
        if rows[0] == 0:
            return 0
        return int(connection.execute("SELECT count(*) FROM configurations").fetchone()[0])
    finally:
        connection.close()


def _invalid_package_data() -> dict[str, Any]:
    """Return a package with two independent, unrelated defects."""
    data = package_data()
    data["configuration"]["source"]["settings"]["url"] = "netbox.example.test"
    data["configuration"]["destination"]["settings"]["bogus_dest"] = "x"
    return data


def test_register_returns_the_configuration_and_its_first_version(tmp_path: Path) -> None:
    location = _store(tmp_path)

    registered = configs_service.register(package=package_data(), product_cache_location=location)

    assert registered.version.registry_version == 1
    assert registered.configuration.config_id == registered.version.config_id
    stored = local_product_projection(tmp_path / "product-cache").lookup_configuration_version(
        registered.version.config_id, 1
    )
    assert stored.value is not None
    assert stored.value.package_checksum == registered.version.package_checksum


def test_register_refuses_an_invalid_package_and_persists_nothing(tmp_path: Path) -> None:
    location = _store(tmp_path)

    with pytest.raises(configs_service.ConfigsValidationError) as raised:
        configs_service.register(package=_invalid_package_data(), product_cache_location=location)

    assert raised.value.family == "validation"
    codes = [finding.code for finding in raised.value.findings]
    assert "endpoint-not-absolute" in codes
    assert "undeclared-setting" in codes
    assert _registered_configuration_count(tmp_path / "product-cache") == 0


def test_a_warning_only_package_registers_and_reports_its_warnings(tmp_path: Path) -> None:
    # Errors prevent execution; warnings do not. A package whose only findings are
    # warnings registers, versions, and validates error-free through the whole service.
    location = _store(tmp_path)
    data = package_data()
    data["omissions"] = [{"kind": "InfraDevice", "fields": ["serial_number"]}]
    changed = package_data()
    changed["omissions"] = [{"kind": "InfraDevice"}]

    registered = configs_service.register(package=data, product_cache_location=location)
    versioned = configs_service.create_version(
        config_id=registered.configuration.config_id,
        package=changed,
        product_cache_location=location,
    )
    report = configs_service.validate(
        config_id=registered.configuration.config_id,
        registry_version=registered.version.registry_version,
        product_cache_location=location,
    )

    assert versioned.created
    assert versioned.version.registry_version == 2
    assert [(finding.code, finding.severity, finding.location) for finding in report.findings] == [
        ("intentional-omission", "warning", "/omissions/0"),
    ]


def test_create_version_is_idempotent_for_an_identical_package(tmp_path: Path) -> None:
    location = _store(tmp_path)
    registered = configs_service.register(package=package_data(), product_cache_location=location)

    repeated = configs_service.create_version(
        config_id=registered.version.config_id,
        package=package_data(),
        product_cache_location=location,
    )

    assert repeated.created is False
    assert repeated.version.registry_version == 1


def test_create_version_allocates_the_next_ordinal_for_new_content(tmp_path: Path) -> None:
    location = _store(tmp_path)
    registered = configs_service.register(package=package_data(), product_cache_location=location)
    changed = package_data()
    changed["configuration"]["source"]["settings"]["url"] = "https://second.netbox.test"

    added = configs_service.create_version(
        config_id=registered.version.config_id,
        package=changed,
        product_cache_location=location,
    )

    assert added.created is True
    assert added.version.registry_version == 2


def test_create_version_refuses_an_unregistered_configuration(tmp_path: Path) -> None:
    with pytest.raises(configs_service.ConfigsNotFoundError) as raised:
        configs_service.create_version(
            config_id="20260808T1200-aaaaaaaa",
            package=package_data(),
            product_cache_location=_store(tmp_path),
        )

    assert raised.value.family == "not-found"


def test_validate_reports_every_defect_of_a_registered_version_in_sorted_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    location = _store(tmp_path)
    registered = configs_service.register(package=package_data(), product_cache_location=location)
    # A registered package was valid when it was registered; re-validating it against a
    # different adapter set is the whole reason this surface exists.
    monkeypatch.setattr("infrahub_sync.configuration.validation.BUILTIN_ADAPTER_CAPABILITIES", {})

    report = configs_service.validate(
        config_id=registered.version.config_id,
        registry_version=1,
        product_cache_location=location,
    )

    assert [(finding.code, finding.location) for finding in report.findings] == [
        ("missing-adapter", "/configuration/destination"),
        ("missing-adapter", "/configuration/source"),
    ]
    assert report.package_checksum == registered.version.package_checksum


def test_validate_reports_no_findings_for_a_still_valid_version(tmp_path: Path) -> None:
    location = _store(tmp_path)
    registered = configs_service.register(package=package_data(), product_cache_location=location)

    report = configs_service.validate(
        config_id=registered.version.config_id,
        registry_version=1,
        product_cache_location=location,
    )

    assert report.findings == ()
    assert BUILTIN_ADAPTER_CAPABILITIES  # the real capability set was in play


def test_validate_refuses_an_unregistered_version(tmp_path: Path) -> None:
    location = _store(tmp_path)
    registered = configs_service.register(package=package_data(), product_cache_location=location)

    with pytest.raises(configs_service.ConfigsNotFoundError) as raised:
        configs_service.validate(
            config_id=registered.version.config_id,
            registry_version=7,
            product_cache_location=location,
        )

    assert raised.value.family == "not-found"


def test_unparseable_package_content_is_a_request_refusal(tmp_path: Path) -> None:
    with pytest.raises(configs_service.ConfigsRequestError) as raised:
        configs_service.register(package={"format_version": 99}, product_cache_location=_store(tmp_path))

    assert raised.value.family == "request"


def test_the_error_vocabulary_is_one_closed_family_set() -> None:
    families = {
        subclass.family
        for subclass in (
            configs_service.ConfigsRequestError,
            configs_service.ConfigsValidationError,
            configs_service.ConfigsNotFoundError,
            configs_service.ConfigsStorageError,
        )
    }

    assert families == {"request", "validation", "not-found", "storage"}
    assert all(
        issubclass(subclass, configs_service.ConfigsError)
        for subclass in (
            configs_service.ConfigsRequestError,
            configs_service.ConfigsValidationError,
            configs_service.ConfigsNotFoundError,
            configs_service.ConfigsStorageError,
        )
    )
    assert configs_service.describe(configs_service.ConfigsNotFoundError("gone"), ()) == "not-found: gone"


def test_a_missing_store_location_is_a_refusal_rather_than_a_fallback() -> None:
    with pytest.raises(configs_service.ConfigsRequestError, match="product_cache_location is required"):
        configs_service.validate(config_id="c", registry_version=1, product_cache_location="")


def test_a_relative_store_location_is_refused() -> None:
    with pytest.raises(configs_service.ConfigsRequestError, match="must be absolute after user expansion"):
        configs_service.validate(config_id="c", registry_version=1, product_cache_location="relative/product-cache")


# --- Registry reads ---------------------------------------------------------------------
#
# The rows below are chosen so raw insertion order disagrees with the declared listing order
# on both halves of the ORDER BY. ``created_at`` is server-generated, so on an append-only
# table insertion order always equals timestamp order and a missing ORDER BY would pass
# trivially: the clock is therefore controlled so two rows share one ``created_at`` (only the
# ``config_id`` tiebreak separates them) and a third row is registered last with the earliest
# timestamp (only ordering by ``created_at`` puts it first).
_OUT_OF_ORDER_REGISTRATIONS: tuple[tuple[str, datetime], ...] = (
    ("config-b", datetime(2026, 8, 8, 12, 30, tzinfo=timezone.utc)),
    ("config-a", datetime(2026, 8, 8, 12, 30, tzinfo=timezone.utc)),
    ("config-z", datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)),
)


def _register_out_of_order(location: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Register the rows above through the service, with generated IDs and clock pinned."""
    ids = iter([config_id for config_id, _ in _OUT_OF_ORDER_REGISTRATIONS])
    clock = iter([created_at for _, created_at in _OUT_OF_ORDER_REGISTRATIONS])
    monkeypatch.setattr(product_store_store, "_generate_config_id", lambda: next(ids))
    monkeypatch.setattr(product_store_store, "datetime", SimpleNamespace(now=lambda tz: next(clock)))  # noqa: ARG005
    for _ in _OUT_OF_ORDER_REGISTRATIONS:
        configs_service.register(package=package_data(), product_cache_location=location)


def test_list_configs_returns_every_configuration_in_created_at_then_config_id_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every registered configuration exactly once, in the one declared deterministic order."""
    location = _store(tmp_path)
    _register_out_of_order(location, monkeypatch)

    listed = configs_service.list_configs(product_cache_location=location)

    assert [(summary.config_id, summary.created_at) for summary in listed] == [
        ("config-z", datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)),
        ("config-a", datetime(2026, 8, 8, 12, 30, tzinfo=timezone.utc)),
        ("config-b", datetime(2026, 8, 8, 12, 30, tzinfo=timezone.utc)),
    ]
    # Order determinism: a re-read of the same store returns the identical sequence.
    assert configs_service.list_configs(product_cache_location=location) == listed


def test_get_config_and_list_versions_return_one_configurations_own_records(tmp_path: Path) -> None:
    """The summary and the full ascending version lineage, scoped to the requested ID.

    A second, unrelated configuration is registered into the same store so an unscoped read
    would be caught leaking its rows.
    """
    location = _store(tmp_path)
    registered = configs_service.register(package=package_data(), product_cache_location=location)
    changed = package_data()
    changed["configuration"]["source"]["settings"]["url"] = "https://second.netbox.test"
    added = configs_service.create_version(
        config_id=registered.version.config_id,
        package=changed,
        product_cache_location=location,
    )
    unrelated = configs_service.register(package=package_data(), product_cache_location=location)

    summary = configs_service.get_config(
        config_id=registered.version.config_id,
        product_cache_location=location,
    )
    versions = configs_service.list_versions(
        config_id=registered.version.config_id,
        product_cache_location=location,
    )

    assert summary == registered.configuration
    assert versions == (registered.version, added.version)
    assert [version.registry_version for version in versions] == [1, 2]
    assert unrelated.version.config_id not in {version.config_id for version in versions}


def test_get_version_on_a_missing_configuration_names_the_configuration_as_absent(tmp_path: Path) -> None:
    """The first half of the AR3 distinction: no such configuration at all."""
    with pytest.raises(configs_service.ConfigsNotFoundError) as raised:
        configs_service.get_version(
            config_id="missing-configuration",
            registry_version=1,
            product_cache_location=_store(tmp_path),
        )

    assert raised.value.family == "not-found"
    assert raised.value.reason == "configuration-not-found"


def test_get_version_on_a_missing_version_is_distinct_from_a_missing_configuration(tmp_path: Path) -> None:
    """The second half: the configuration exists and the version does not.

    The store's own lookup blends both cases into one "configuration-version-not-found"
    reason, so the service looks the configuration up first (deletion does not exist, so the
    two-step read is race-safe) and the two absences surface as distinct machine-readable
    values -- what service-boundary later maps to two different status codes.
    """
    location = _store(tmp_path)
    registered = configs_service.register(package=package_data(), product_cache_location=location)

    with pytest.raises(configs_service.ConfigsNotFoundError) as missing_version:
        configs_service.get_version(
            config_id=registered.version.config_id,
            registry_version=7,
            product_cache_location=location,
        )
    with pytest.raises(configs_service.ConfigsNotFoundError) as missing_configuration:
        configs_service.get_version(
            config_id="missing-configuration",
            registry_version=7,
            product_cache_location=location,
        )

    assert missing_version.value.family == "not-found"
    assert missing_version.value.reason == "configuration-version-not-found"
    assert missing_version.value.reason != missing_configuration.value.reason


# The two reads whose store queries return a tuple, so a missing configuration's natural
# defect is a silent empty result -- indistinguishable from a real answer about a registered
# configuration with no rows to show.
_MISSING_CONFIGURATION_READS: tuple[tuple[str, Callable[[str], object]], ...] = (
    (
        "get_config",
        lambda location: configs_service.get_config(
            config_id="missing-configuration",
            product_cache_location=location,
        ),
    ),
    (
        "list_versions",
        lambda location: configs_service.list_versions(
            config_id="missing-configuration",
            product_cache_location=location,
        ),
    ),
)


@pytest.mark.parametrize("call", [pytest.param(call, id=name) for name, call in _MISSING_CONFIGURATION_READS])
def test_a_read_of_a_missing_configuration_refuses_rather_than_answering_empty(
    tmp_path: Path,
    call: Callable[[str], object],
) -> None:
    with pytest.raises(configs_service.ConfigsNotFoundError) as raised:
        call(_store(tmp_path))

    assert raised.value.family == "not-found"
    assert raised.value.reason == "configuration-not-found"


def test_get_version_round_trips_the_registered_content_and_checksum(tmp_path: Path) -> None:
    """A read returns exactly what registration reported -- field equality, not "no error"."""
    location = _store(tmp_path)
    registered = configs_service.register(package=package_data(), product_cache_location=location)

    stored = configs_service.get_version(
        config_id=registered.version.config_id,
        registry_version=1,
        product_cache_location=location,
    )

    assert stored.declared_content == registered.version.declared_content
    assert stored.package_checksum == registered.version.package_checksum
    assert stored == registered.version


# The read entry points, each reached with a syntactically fine request, so the only thing a
# raised refusal can be about is the store location (envelope OES-21's evidence pattern).
_READ_ENTRY_POINTS: tuple[tuple[str, Callable[[str | None], object]], ...] = (
    ("list_configs", lambda location: configs_service.list_configs(product_cache_location=location)),
    ("get_config", lambda location: configs_service.get_config(config_id="c", product_cache_location=location)),
    ("list_versions", lambda location: configs_service.list_versions(config_id="c", product_cache_location=location)),
    (
        "get_version",
        lambda location: configs_service.get_version(
            config_id="c",
            registry_version=1,
            product_cache_location=location,
        ),
    ),
)


@pytest.mark.parametrize("call", [pytest.param(call, id=name) for name, call in _READ_ENTRY_POINTS])
def test_a_read_with_a_missing_store_location_is_a_refusal_rather_than_a_fallback(
    call: Callable[[str | None], object],
) -> None:
    with pytest.raises(configs_service.ConfigsRequestError, match="product_cache_location is required"):
        call(None)


@pytest.mark.parametrize("call", [pytest.param(call, id=name) for name, call in _READ_ENTRY_POINTS])
def test_a_read_with_a_relative_store_location_is_refused(call: Callable[[str | None], object]) -> None:
    with pytest.raises(configs_service.ConfigsRequestError, match="must be absolute after user expansion"):
        call("relative/product-cache")


# A value of the wrong type entirely, annotated ``Any`` so the calls below type-check. That is
# the only way it reaches the service: a caller's own defect that got past static checking.
_WRONG_TYPED_VALUE: Any = object()

_WRONG_TYPED_CALLS: tuple[tuple[str, Callable[[str], object]], ...] = (
    (
        "validate-config-id",
        lambda location: configs_service.validate(
            config_id=_WRONG_TYPED_VALUE,
            registry_version=1,
            product_cache_location=location,
        ),
    ),
    (
        "validate-registry-version",
        lambda location: configs_service.validate(
            config_id="c",
            registry_version=_WRONG_TYPED_VALUE,
            product_cache_location=location,
        ),
    ),
    (
        "create-version-config-id",
        lambda location: configs_service.create_version(
            config_id=_WRONG_TYPED_VALUE,
            package=package_data(),
            product_cache_location=location,
        ),
    ),
    (
        "get-config-config-id",
        lambda location: configs_service.get_config(
            config_id=_WRONG_TYPED_VALUE,
            product_cache_location=location,
        ),
    ),
    (
        "list-versions-config-id",
        lambda location: configs_service.list_versions(
            config_id=_WRONG_TYPED_VALUE,
            product_cache_location=location,
        ),
    ),
    (
        "get-version-config-id",
        lambda location: configs_service.get_version(
            config_id=_WRONG_TYPED_VALUE,
            registry_version=1,
            product_cache_location=location,
        ),
    ),
    (
        "get-version-registry-version",
        lambda location: configs_service.get_version(
            config_id="c",
            registry_version=_WRONG_TYPED_VALUE,
            product_cache_location=location,
        ),
    ),
)


@pytest.mark.parametrize("call", [pytest.param(call, id=name) for name, call in _WRONG_TYPED_CALLS])
def test_a_wrong_typed_identifier_is_the_callers_input_and_not_the_store(
    tmp_path: Path,
    call: Callable[[str], object],
) -> None:
    """A wrong-typed identifier must not send an operator to look at their own disk.

    Unguarded, the value is handed to SQLite as a query parameter and comes back as
    ``sqlite3.ProgrammingError``, which the boundary can only read as ``storage``. The store
    here is real, writable and healthy, so ``storage`` is a false report about it: the defect is
    in the call.
    """
    with pytest.raises(configs_service.ConfigsError) as raised:
        call(_store(tmp_path))

    assert raised.value.family == "request"


def test_a_well_typed_identifier_still_gets_the_stores_own_answer(tmp_path: Path) -> None:
    """The type guard checks the type and nothing else, so absence is still the store's verdict."""
    with pytest.raises(configs_service.ConfigsNotFoundError) as raised:
        configs_service.validate(config_id="c", registry_version=1, product_cache_location=_store(tmp_path))

    assert raised.value.family == "not-found"


_ENTRY_POINTS: tuple[tuple[str, Callable[[str], object]], ...] = (
    ("register", lambda location: configs_service.register(package=package_data(), product_cache_location=location)),
    (
        "create_version",
        lambda location: configs_service.create_version(
            config_id="c",
            package=package_data(),
            product_cache_location=location,
        ),
    ),
    (
        "validate",
        lambda location: configs_service.validate(config_id="c", registry_version=1, product_cache_location=location),
    ),
    ("list_configs", lambda location: configs_service.list_configs(product_cache_location=location)),
    ("get_config", lambda location: configs_service.get_config(config_id="c", product_cache_location=location)),
    ("list_versions", lambda location: configs_service.list_versions(config_id="c", product_cache_location=location)),
    (
        "get_version",
        lambda location: configs_service.get_version(
            config_id="c",
            registry_version=1,
            product_cache_location=location,
        ),
    ),
)


@pytest.mark.parametrize("call", [pytest.param(call, id=name) for name, call in _ENTRY_POINTS])
def test_a_store_the_filesystem_refuses_is_a_storage_refusal(
    tmp_path: Path,
    call: Callable[[str], object],
) -> None:
    # A real store failure, not a monkeypatched raise. Opening the registry creates its own
    # directories, and the filesystem is what refuses here, so this test can see which base
    # class the service catches - which a faked raise cannot. A plain file where the registry
    # wants a directory is the form that needs no permission change, so it behaves identically
    # as root and under CI, where an unwritable-directory test would not fail at all.
    occupied = tmp_path / "product-cache"
    occupied.write_text("not a directory\n", encoding="utf-8")

    with pytest.raises(configs_service.ConfigsStorageError) as raised:
        call(str(occupied))

    assert raised.value.family == "storage"


def test_a_package_file_is_loaded_from_json_or_yaml(tmp_path: Path) -> None:
    json_file = tmp_path / "package.json"
    json_file.write_text('{"format_version": 1}', encoding="utf-8")
    yaml_file = tmp_path / "package.yml"
    yaml_file.write_text("format_version: 1\n", encoding="utf-8")

    assert configs_service.load_package_content(json_file) == {"format_version": 1}
    assert configs_service.load_package_content(yaml_file) == {"format_version": 1}


def test_an_unreadable_package_file_is_a_request_refusal(tmp_path: Path) -> None:
    with pytest.raises(configs_service.ConfigsRequestError):
        configs_service.load_package_content(tmp_path / "absent.json")


def test_a_package_file_too_nested_to_parse_is_a_request_refusal(tmp_path: Path) -> None:
    # A real parse failure, not a monkeypatched raise. yaml.safe_load recurses once per nesting
    # level, so a deeply nested file exhausts the interpreter stack and raises RecursionError -
    # which is not a yaml.YAMLError and is not a defect in the declared package either. It is
    # the caller's file that cannot be read, so it belongs to the request family like every
    # other unusable input.
    depth = sys.getrecursionlimit()
    nested = tmp_path / "nested.yaml"
    nested.write_text("[" * depth + "]" * depth, encoding="utf-8")

    with pytest.raises(configs_service.ConfigsRequestError) as raised:
        configs_service.load_package_content(nested)

    assert raised.value.family == "request"


# --- One error boundary, so the declared vocabulary is total -----------------------------
#
# What is asserted below is the property, not a list of known escapes: any exception leaving
# a public service operation is a ConfigsError from the declared vocabulary. Wherever a real
# failure is available it is caused for real - a registry file that is not a database, a file
# saved in the wrong encoding, a file describing an infinite structure - because this defect
# class survived two adversarial reviews on tests that only ever faked the raise, and a faked
# raise cannot see which base class production code actually catches. Only the unforeseen
# arm, which by definition no real dependency reaches, is driven by a monkeypatched raise.

_DECLARED_FAMILIES = frozenset({"request", "validation", "not-found", "storage", "internal"})

# The text helpers on the service's public surface. Each transforms text the service itself
# produced - or answers which rule governs one field of it - takes no store and no path, and
# is what an interface calls to render a refusal that already exists, so a family label on one
# would name nothing. Every other public function is an operation and must carry the boundary.
_TEXT_HELPERS = frozenset(
    {
        "describe",
        "is_closed_vocabulary_field",
        "redact_finding",
        "redact_message",
        "redact_pointer",
        "redact_public_field",
    }
)


class _UnforeseenError(Exception):
    """A dependency failure no arm in the service names, standing in for the ones nobody has met."""


def _raise_unforeseen(*args: object, **kwargs: object) -> object:
    del args, kwargs
    msg = "Zq7"
    raise _UnforeseenError(msg)


def test_a_package_file_saved_in_another_encoding_is_a_request_refusal(tmp_path: Path) -> None:
    # A real failure: the bytes really are CP1252. This is the likeliest of these mistakes to
    # reach an operator - a package edited on Windows and saved as-is - and read_text raises
    # UnicodeDecodeError, which is a ValueError and not an OSError, so the arm one line above
    # it never saw it.
    path = tmp_path / "cp1252.yaml"
    path.write_bytes("format_version: 1\ndescription: café\n".encode("cp1252"))

    with pytest.raises(configs_service.ConfigsRequestError) as raised:
        configs_service.load_package_content(path)

    assert raised.value.family == "request"


def test_a_recursive_package_file_is_a_request_refusal(tmp_path: Path) -> None:
    # A real failure: an anchor that contains itself is legal YAML describing an infinite
    # structure. safe_load returns it, and the JSON round-trip is where it breaks - json.dumps
    # raises ValueError("Circular reference detected"), which the coercion probe swallows and
    # the coercion itself did not guard. The caller's file is what cannot be read as a
    # package, so it is a request refusal like every other unusable input.
    path = tmp_path / "recursive.yaml"
    path.write_text("&anchor\nkey: *anchor\n", encoding="utf-8")

    with pytest.raises(configs_service.ConfigsRequestError) as raised:
        configs_service.load_package_content(path)

    assert raised.value.family == "request"


def test_a_package_file_whose_key_json_cannot_hold_is_a_request_refusal(tmp_path: Path) -> None:
    # The same arm, a second real shape: an unquoted YAML date is a date, and a date used as a
    # mapping key is a TypeError from json.dumps, because default=str stringifies values and
    # never keys. It arrives at the same place as the recursive file and for the same reason -
    # the coercion probe reads every dump failure as "coercion needed".
    path = tmp_path / "dated-key.yaml"
    path.write_text("? 2024-01-01\n: value\n", encoding="utf-8")

    with pytest.raises(configs_service.ConfigsRequestError) as raised:
        configs_service.load_package_content(path)

    assert raised.value.family == "request"


def _corrupt_registry(tmp_path: Path) -> str:
    """Return a store location whose registry file exists, is readable, and is not a database."""
    root = tmp_path / "product-cache"
    root.mkdir()
    (root / "product-records.sqlite3").write_bytes(b"this file is not a database\n")
    return str(root)


@pytest.mark.parametrize("call", [pytest.param(call, id=name) for name, call in _ENTRY_POINTS])
def test_a_corrupt_registry_file_is_a_storage_refusal(tmp_path: Path, call: Callable[[str], object]) -> None:
    # A real failure, and a cheap one: SQLite does not read the file header when it opens, so
    # the store is constructed successfully and every *query* raises sqlite3.DatabaseError
    # afterwards. The arms around each store call name the store's own error types, so all
    # three operations handed the caller a raw sqlite3 traceback.
    with pytest.raises(configs_service.ConfigsStorageError) as raised:
        call(_corrupt_registry(tmp_path))

    assert raised.value.family == "storage"


def _load_a_written_package(tmp_path: Path) -> object:
    """Load one legal package file, so only the patched dependency can fail the call."""
    path = tmp_path / "package.yaml"
    path.write_text("format_version: 1\n", encoding="utf-8")
    return configs_service.load_package_content(path)


_PUBLIC_OPERATIONS: tuple[tuple[str, Callable[[pytest.MonkeyPatch], None], Callable[[Path], object]], ...] = (
    (
        "load_package_content",
        lambda patch: patch.setattr(configs_service.yaml, "safe_load", _raise_unforeseen),
        _load_a_written_package,
    ),
    (
        "register",
        lambda patch: patch.setattr(configs_service, "local_product_projection", _raise_unforeseen),
        lambda tmp_path: configs_service.register(package=package_data(), product_cache_location=_store(tmp_path)),
    ),
    (
        "create_version",
        lambda patch: patch.setattr(configs_service, "local_product_projection", _raise_unforeseen),
        lambda tmp_path: configs_service.create_version(
            config_id="c",
            package=package_data(),
            product_cache_location=_store(tmp_path),
        ),
    ),
    (
        "validate",
        lambda patch: patch.setattr(configs_service, "local_product_projection", _raise_unforeseen),
        lambda tmp_path: configs_service.validate(
            config_id="c",
            registry_version=1,
            product_cache_location=_store(tmp_path),
        ),
    ),
    (
        "list_configs",
        lambda patch: patch.setattr(configs_service, "local_product_projection", _raise_unforeseen),
        lambda tmp_path: configs_service.list_configs(product_cache_location=_store(tmp_path)),
    ),
    (
        "get_config",
        lambda patch: patch.setattr(configs_service, "local_product_projection", _raise_unforeseen),
        lambda tmp_path: configs_service.get_config(config_id="c", product_cache_location=_store(tmp_path)),
    ),
    (
        "list_versions",
        lambda patch: patch.setattr(configs_service, "local_product_projection", _raise_unforeseen),
        lambda tmp_path: configs_service.list_versions(config_id="c", product_cache_location=_store(tmp_path)),
    ),
    (
        "get_version",
        lambda patch: patch.setattr(configs_service, "local_product_projection", _raise_unforeseen),
        lambda tmp_path: configs_service.get_version(
            config_id="c",
            registry_version=1,
            product_cache_location=_store(tmp_path),
        ),
    ),
)


@pytest.mark.parametrize(
    ("patch_dependency", "call"),
    [pytest.param(patch, call, id=name) for name, patch, call in _PUBLIC_OPERATIONS],
)
def test_a_failing_dependency_leaves_a_public_operation_inside_the_declared_vocabulary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    patch_dependency: Callable[[pytest.MonkeyPatch], None],
    call: Callable[[Path], object],
) -> None:
    # The one place a monkeypatched raise is the right tool: this drives the *unforeseen* arm,
    # and an exception type the service has never met is by definition one no real dependency
    # raises today. Every other failure in this section is caused for real.
    patch_dependency(monkeypatch)

    with pytest.raises(configs_service.ConfigsError) as raised:
        call(tmp_path)

    assert raised.value.family in _DECLARED_FAMILIES
    # The documented default: neither the caller's input nor the store, and it says so rather
    # than sending an operator to check a disk over a defect in this service.
    assert raised.value.family == "internal"


def _raise_interrupt(*args: object, **kwargs: object) -> object:
    del args, kwargs
    raise KeyboardInterrupt


def test_an_interrupt_is_not_caught_by_the_error_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The boundary catches Exception, never BaseException, so an interrupt reaches the
    # interpreter instead of being reported as a registry refusal. Widening it would swallow
    # Ctrl-C and SystemExit, which is the decision the run boundary already made explicitly,
    # and nothing else here would notice.
    monkeypatch.setattr(configs_service, "local_product_projection", _raise_interrupt)

    with pytest.raises(KeyboardInterrupt):
        configs_service.register(package=package_data(), product_cache_location=_store(tmp_path))


def _public_service_functions() -> dict[str, Any]:
    """Return every function the service module itself exposes without a leading underscore."""
    return {
        name: value
        for name, value in vars(configs_service).items()
        if not name.startswith("_") and inspect.isfunction(value) and value.__module__ == configs_service.__name__
    }


def test_every_public_service_operation_carries_the_error_boundary() -> None:
    # The general claim, checked structurally rather than case by case, so coverage is not four
    # names kept in step by hand. A public operation added later without the boundary fails
    # here, and the only way past is to call it a text helper and say why.
    public = _public_service_functions()
    guarded = set(configs_service._GUARDED_OPERATIONS)

    assert guarded <= set(public)
    assert set(public) - guarded == _TEXT_HELPERS
    assert guarded == {
        "create_version",
        "get_config",
        "get_version",
        "list_configs",
        "list_versions",
        "load_package_content",
        "register",
        "validate",
    }


def _refusal_types(root: type[configs_service.ConfigsError]) -> set[type[configs_service.ConfigsError]]:
    """Return every refusal type the service module defines below `root`, however nested."""
    direct = {subclass for subclass in root.__subclasses__() if subclass.__module__ == configs_service.__name__}
    return direct | {nested for subclass in direct for nested in _refusal_types(subclass)}


def test_every_refusal_type_the_service_defines_carries_a_documented_family() -> None:
    # The closed-family-set test above names its four types, so it cannot notice a fifth. This
    # one enumerates, so a family added to the module without a documented name fails here.
    families = {subclass.family for subclass in _refusal_types(configs_service.ConfigsError)}

    assert families == _DECLARED_FAMILIES


# Two pointers whose only difference is a component that is itself a current credential value.
# Redaction replaces exactly that component, which is the shape in which a lossy pointer
# transformation can collapse two separate declared defects into one reported finding.
_COLLIDING_SECRETS = ("alpha", "beta")

_POINTER_BOUND = 256


def _settings_finding(component: str) -> ValidationFinding:
    """Build one finding whose pointer ends in `component` and whose message quotes it."""
    return ValidationFinding(
        code="undeclared-setting",
        severity="error",
        location=f"/configuration/source/settings/{component}",
        message=f"setting {component}",
    )


def test_two_pointers_that_redact_alike_stay_distinguishable() -> None:
    """Redaction is lossy, so it obeys the rule truncation obeys: no two pointers collapse into one.

    The core's invariant is intact either way — dedup and precedence run in `collect_findings`,
    before redaction — so what breaks here is the operator's ability to tell two reported
    defects apart at the presentation boundary, which is the same failure truncation had.
    """
    first, second = (
        configs_service.redact_finding(_settings_finding(name), _COLLIDING_SECRETS) for name in _COLLIDING_SECRETS
    )

    assert first.location != second.location
    assert first != second
    for finding, collected in zip((first, second), _COLLIDING_SECRETS):
        assert collected not in finding.location
        assert finding.location.startswith("/configuration/source/settings/***")
        # Still a pointer the field's own grammar accepts, which is what redacting a component
        # at a time buys and what appending anything to the result could otherwise cost.
        assert ValidationFinding.model_validate(finding.model_dump()) == finding


def test_a_redacted_pointer_is_distinguished_by_its_path_not_by_its_secret() -> None:
    """The distinguishing tag is bound to the pointer, so it is not a fingerprint of a value.

    One credential value at two declared paths must not produce one tag: a value-only tag is
    the same wherever that value appears, in any report, and can be joined against a table of
    hashed candidate values.
    """
    collected = "alpha"

    source = configs_service.redact_pointer(f"/configuration/source/settings/{collected}", [collected])
    destination = configs_service.redact_pointer(f"/configuration/destination/settings/{collected}", [collected])

    assert source.removeprefix("/configuration/source") != destination.removeprefix("/configuration/destination")


def test_a_redacted_pointer_stays_inside_the_pointer_bound() -> None:
    """Whatever redaction appends is inside the bound the pointer field enforces."""
    collected = "alpha"
    location = "/" + "a" * 200 + f"/{collected}/" + "b" * 40

    redacted = configs_service.redact_pointer(location, [collected])

    assert len(redacted) <= _POINTER_BOUND
    assert ValidationFinding(code="undeclared-setting", severity="error", location=redacted, message="x")


def test_a_redacted_pointer_cut_on_an_escape_is_still_a_legal_pointer() -> None:
    """The re-applied bound can separate a "~" from the character that completes it.

    Redaction rebuilds the pointer and then re-applies the field's own 256-character bound, and
    that second cut lands wherever the shortened text puts it. Here it falls on the "~" of a
    "~1" escape, so without dropping the orphaned "~" the returned pointer is one the finding
    grammar refuses — a pydantic error at both interfaces instead of a redacted result. The
    offsets are chosen so a legal input pointer produces exactly that cut.
    """
    collected = "abcdef"
    location = f"/{collected}/" + "a" * 239 + "~1" + "z" * 5

    assert _accepts(location)

    redacted = configs_service.redact_pointer(location, [collected])

    assert collected not in redacted
    assert len(redacted) <= _POINTER_BOUND
    assert _accepts(redacted)


def test_an_unredacted_pointer_is_returned_unchanged() -> None:
    """Nothing is appended when nothing was replaced: only a lossy pass needs a tag."""
    location = "/configuration/source/settings/url"

    assert configs_service.redact_pointer(location, ["alpha"]) == location


# A pointer component is an *escaped* declared key: "~0" stands for a literal "~" and "~1" for
# a literal "/". The cases below are the ones that separate redacting the key from redacting
# the escaped text of the key.
_TILDE_KEY = "x~abcdefg"
_SLASH_KEY = "a/babcde"


def _escaped(key: str) -> str:
    """Return one declared key in the escaped form a pointer component actually carries."""
    return safe_pointer_component(key)


def _accepts(location: str) -> bool:
    """Return whether the pointer field's own grammar accepts `location`."""
    try:
        ValidationFinding(code="undeclared-setting", severity="error", location=location, message="x")
    except ValidationError:
        return False
    return True


@pytest.mark.parametrize(
    ("key", "collected"),
    [
        pytest.param(_TILDE_KEY, "0abcde", id="escaped-tilde"),
        pytest.param(_SLASH_KEY, "1babcd", id="escaped-slash"),
    ],
)
def test_a_collected_value_cannot_orphan_an_escape_in_a_pointer(key: str, collected: str) -> None:
    """A collected value that overlaps an escape must not leave a "~" the grammar refuses.

    "0abcde" and "1babcd" only appear in the *escaped* text of the key, straddling the escape's
    "~" and the character that completes it. Replacing there consumes the completing character
    and orphans the "~", so the pointer is refused — which raises a pydantic error at both
    interfaces instead of returning a result. The value is not in the declared key at all, so
    the correct answer is that nothing is replaced.
    """
    location = f"/configuration/source/settings/{_escaped(key)}"

    redacted = configs_service.redact_pointer(location, [collected])

    assert _accepts(redacted)
    assert redacted == location


def test_a_collected_value_containing_a_separator_is_redacted_from_the_key() -> None:
    """A value is matched against the declared key, not against the key's escaped text.

    A credential value carrying "/" or "~" is invisible in the escaped form, so redacting the
    escaped text leaves it in the report. Redacting the key catches it, and re-escaping
    afterwards keeps the result a legal pointer.
    """
    collected = "w/secret1"
    location = f"/configuration/source/settings/{_escaped('pw/secret123')}"

    redacted = configs_service.redact_pointer(location, [collected])

    assert collected not in redacted
    assert _accepts(redacted)
    assert redacted.startswith("/configuration/source/settings/p***23")


def test_a_collected_value_spanning_a_component_boundary_is_not_replaced() -> None:
    """The "/" between two components is structure, not declared content.

    No declared key contains it, so a value that only appears across the boundary is not a
    value the pointer discloses, and the pointer is returned untouched.
    """
    location = "/configuration/foo/barbaz"

    assert configs_service.redact_pointer(location, ["oo/barba"]) == location


def test_a_pointer_carrying_escaped_separators_is_returned_unchanged() -> None:
    """A key holding both escapes and no collected value comes back byte-identical."""
    location = f"/configuration/source/settings/{_escaped('a~1b/c')}"

    assert location == "/configuration/source/settings/a~01b~1c"
    assert configs_service.redact_pointer(location, ["nosecrethere"]) == location


# ``_add_url_userinfo`` collects a credential in "user:password" form, so a colon is part of
# the most common collected-secret shape there is. ``safe_pointer_component`` writes ":" as
# "\u003a", and that escaped text — not the declared key — is what the emitted pointer carries.
_USERINFO_SHAPED_KEY = "adm:pw"

# Every declared key whose escaped form differs from the key itself, so a redaction pass that
# matches against the escaped text misses the value the key holds.
_ESCAPED_SECRET_KEYS = (
    pytest.param("adm:pw", id="colon"),
    pytest.param("adm;pw", id="semicolon"),
    pytest.param("adm\\pw", id="backslash"),
    pytest.param("adm\tpw", id="tab"),
    pytest.param("adm\npw", id="newline"),
    pytest.param("adm\rpw", id="carriage-return"),
    pytest.param("adm/pw", id="slash"),
    pytest.param("adm~pw", id="tilde"),
    pytest.param("adm\x01pw", id="control-character"),
)


def test_a_userinfo_shaped_secret_is_redacted_from_the_key_it_escapes_into() -> None:
    """The most common collected-secret shape is the one the escaped text hides.

    ``_add_url_userinfo`` collects "user:password", ``safe_pointer_component`` writes the colon
    as "\u003a", and matching against that text never finds the value. The declared key is
    fully recoverable from the emitted pointer, in a mechanism whose docstring says it redacts
    the declared keys a pointer quotes.
    """
    location = f"/configuration/source/settings/{_escaped(_USERINFO_SHAPED_KEY)}"

    redacted = configs_service.redact_pointer(location, [_USERINFO_SHAPED_KEY])

    assert redacted != location
    assert _USERINFO_SHAPED_KEY not in redacted
    assert _escaped(_USERINFO_SHAPED_KEY) not in redacted
    assert redacted.startswith("/configuration/source/settings/***")
    assert _accepts(redacted)


@pytest.mark.parametrize("key", _ESCAPED_SECRET_KEYS)
def test_a_secret_the_escape_table_hides_is_still_redacted(key: str) -> None:
    """Every entry in the escape table, not only the two separators, is inverted for matching."""
    location = f"/configuration/source/settings/{_escaped(key)}"

    redacted = configs_service.redact_pointer(location, [key])

    assert redacted != location
    assert _escaped(key) not in redacted
    assert redacted.startswith("/configuration/source/settings/***")
    assert _accepts(redacted)


# A finding *message* quotes the same declared key the pointer does, and quotes it escaped. Two
# producers write it, in two different encodings, so the property below is asserted against the
# forms spelled out here rather than against either producer's output.
_MESSAGE_PRODUCER_CODES = frozenset({"undeclared-setting", "credential-path-not-declared"})


def _tree_forms(key: str) -> tuple[str, ...]:
    """Every form the core can write one declared key as, derived independently of redaction.

    The declared key itself; the escaped pointer component ``_bounded_location`` embeds; and
    that escaped text run through ``json.dumps`` the way ``_render_setting_name_list`` runs it,
    which doubles every backslash. Built here from the core's own escaping function and the
    standard library, so weakening the redaction side cannot weaken what is asserted.
    """
    escaped = safe_pointer_component(key)
    return (key, escaped, json.dumps(escaped, ensure_ascii=True)[1:-1])


def _findings_for_declared_key(key: str) -> tuple[ValidationFinding, ...]:
    """Return the real findings a package declaring `key` as a setting name produces.

    Two, and deliberately two, because they are the two message producers: an undeclared
    setting, whose message renders the escaped name through ``json.dumps``; and a
    ``$credential`` node nested under an allowed setting, whose message embeds the escaped
    pointer directly.
    """
    data = package_data()
    settings = data["configuration"]["source"]["settings"]
    settings[key] = 1
    settings["verify_ssl"] = {key: {"$credential": "netbox-token"}}
    return collect_findings(package(data))


@pytest.mark.parametrize("key", _ESCAPED_SECRET_KEYS)
def test_a_secret_the_escape_table_hides_is_redacted_from_the_message_too(key: str) -> None:
    """No collected value survives in a finding's message in any form the core writes it as.

    The pointer is matched escape-aware and the message was not, so the same declared key the
    pointer hardening exists for walked through the message on the same output line — and a
    message reaches CLI stdout, the abort log, ``ConfigsError.message`` and every returned
    finding. Asserted as the property over the whole escape table and both encodings, not as
    the two shapes that were found.
    """
    findings = _findings_for_declared_key(key)

    redacted = [configs_service.redact_finding(finding, [key]) for finding in findings]

    # Not vacuous: both producers really ran, so neither encoding is missing from the sample.
    assert {finding.code for finding in findings} == _MESSAGE_PRODUCER_CODES
    for finding in redacted:
        for form in _tree_forms(key):
            assert form not in finding.message
            assert form not in finding.location
        assert ValidationFinding.model_validate(finding.model_dump()) == finding


def test_the_two_validation_message_producers_each_write_a_different_encoding() -> None:
    """The sample the property runs over really contains both encodings, spelled apart.

    Guards the parametrised test above from passing because one producer stopped embedding the
    key at all: the escaped form and the JSON-doubled form differ for a key holding a colon,
    and each is present in exactly the message that produces it.

    Two *validation* message producers, which is not the producer set. Nothing here bounds how
    many renderers the core has; :func:`redact_message` records the two that are known to sit
    outside what matching covers.
    """
    _, escaped, json_doubled = _tree_forms(_USERINFO_SHAPED_KEY)
    messages = {finding.code: finding.message for finding in _findings_for_declared_key(_USERINFO_SHAPED_KEY)}

    assert escaped != json_doubled
    assert escaped in messages["credential-path-not-declared"]
    assert json_doubled in messages["undeclared-setting"]


def test_a_message_form_is_replaced_before_any_shorter_form_contained_in_it() -> None:
    """The order the forms come back in is part of the mechanism, not a detail of the sort.

    Two collected values can share a prefix — "user:password" and "user:passwor" both reach
    ``_add_url_userinfo`` from two endpoint variables that differ by one character — and each
    is written into a message in the same encodings. Replacing the shorter one first leaves the
    tail of the longer one on the line, in every encoding at once, so the forms are returned
    longest-first the way ``collect_secret_values`` returns the values themselves.
    """
    longer, shorter = "abc:def", "abc:de"
    text = f'setting "{safe_pointer_component(longer)}" and {longer} is undeclared'

    redacted = configs_service.redact_message(text, (longer, shorter))

    # Not vacuous: the shorter value is a real collection target and a prefix of the longer one.
    assert len(shorter) >= MIN_SECRET_LENGTH
    assert longer.startswith(shorter)
    assert redacted == f'setting "{REDACTED}" and {REDACTED} is undeclared'


def test_the_shipped_collector_really_produces_the_value_the_property_uses() -> None:
    """The six-character userinfo value is one the runtime collector emits, not a test fixture.

    ``_add_url_userinfo`` is name-blind, so an endpoint variable carrying userinfo is where this
    shape comes from, and it clears ``MIN_SECRET_LENGTH`` — the property is therefore about a
    value that reaches redaction in a real run.
    """
    collected = collect_secret_values(environ={"NETBOX_ADDRESS": f"https://{_USERINFO_SHAPED_KEY}@netbox.test/api"})

    assert _USERINFO_SHAPED_KEY in collected


# Below the collector's floor, so ``collect_secret_values`` would never hand this to redaction.
# It is used to show what the message text carries, which is what makes the property above a
# real exposure rather than an artifact of a value chosen to be long enough.
_SUB_FLOOR_KEY = "a:b"


def test_a_finding_message_carries_the_declared_key_it_quotes() -> None:
    """The message genuinely embeds caller content, escaped — the property is not about nothing.

    A sub-floor value proves the channel: unredacted, both producers carry the declared key in
    the encoding each writes, and redaction removes it when it is supplied. Length is the
    collector's rule, not this mechanism's.
    """
    findings = _findings_for_declared_key(_SUB_FLOOR_KEY)
    _, escaped, json_doubled = _tree_forms(_SUB_FLOOR_KEY)
    messages = {finding.code: finding.message for finding in findings}

    assert len(_SUB_FLOOR_KEY) < MIN_SECRET_LENGTH
    assert _SUB_FLOOR_KEY not in collect_secret_values(environ={"NETBOX_ADDRESS": f"https://{_SUB_FLOOR_KEY}@h/api"})
    assert escaped in messages["credential-path-not-declared"]
    assert json_doubled in messages["undeclared-setting"]
    for finding in findings:
        redacted = configs_service.redact_finding(finding, [_SUB_FLOOR_KEY])
        for form in _tree_forms(_SUB_FLOOR_KEY):
            assert form not in redacted.message


# The three keys that separate a correct inversion from a plausible one. Each is written the
# way a declared key would be, and each escapes into text an incorrect decoder mis-reads.
_HOSTILE_KEYS = (
    pytest.param("a\\b", id="a-literal-backslash"),
    pytest.param("a\\u003ab", id="a-colon-written-as-six-characters"),
    pytest.param("a\x01b", id="a-control-character"),
)


@pytest.mark.parametrize("key", _HOSTILE_KEYS)
def test_a_hostile_key_survives_reassembly_byte_for_byte(key: str) -> None:
    """Re-encoding restores exactly what was decoded, for the keys full inversion could break.

    A literal backslash escapes to "\\\\", the six characters "\\u003a" escape to "\\\\u003a", and a
    control character escapes to "\\u0001" — three different readings of one leading backslash.
    A decoder that inverts the table without also parsing the "\\uXXXX" form conflates them, and
    the component comes back changed. Redacting the *neighbouring* component is what forces the
    reassembly path; a pointer with nothing replaced is returned without being rebuilt at all.
    """
    location = f"/configuration/source/settings/{_escaped(key)}/nb1"

    redacted = configs_service.redact_pointer(location, ["nb1"])

    assert redacted.startswith(f"/configuration/source/settings/{_escaped(key)}/***")
    assert _accepts(redacted)


@pytest.mark.parametrize("key", _HOSTILE_KEYS)
def test_a_hostile_key_is_matched_as_the_key_and_not_as_its_escaped_text(key: str) -> None:
    """The decoded component is the declared key itself, so a secret equal to it is caught."""
    location = f"/configuration/source/settings/{_escaped(key)}"

    redacted = configs_service.redact_pointer(location, [key])

    assert redacted.startswith("/configuration/source/settings/***")
    assert _accepts(redacted)


# The truncation marker as the core writes it — six characters, because U+2026 is printable
# and ``safe_pointer_component`` therefore never produces this form from a declared key.
_TRUNCATION_MARKER_ESCAPE = "\\u2026"
_TRUNCATION_MARKER_CHARACTER = "\u2026"


def _two_truncated_pointers() -> tuple[ValidationFinding, ValidationFinding]:
    """Two findings whose declared keys differ only past the 64-character component bound."""
    data = package_data()
    shared = "k" * 70
    data["configuration"]["source"]["settings"][f"{shared}a"] = 1
    data["configuration"]["source"]["settings"][f"{shared}b"] = 1
    first, second = collect_findings(package(data))
    return first, second


def test_a_truncated_pointer_that_is_then_redacted_stays_distinguishable() -> None:
    """Redaction digests a pointer truncation has already cut, and distinctness still composes.

    The concern is that the redaction digest is taken over text that has itself lost
    information, so two pointers separated only by what truncation dropped could collapse.
    They cannot: truncation closed that gap where it was opened, by writing a digest of the
    *untruncated* pointer into the text it hands on. Redaction's input is therefore already
    distinct, and the surviving truncation digest is what keeps the two apart even before
    redaction appends a tag of its own.
    """
    findings = _two_truncated_pointers()
    redacted = [configs_service.redact_pointer(finding.location, ["kkk"]) for finding in findings]

    assert findings[0].location != findings[1].location
    assert redacted[0] != redacted[1]
    # Not the redaction tag doing the work: drop it and the two are still different, because
    # the digest truncation wrote survives the redaction pass.
    assert redacted[0].rsplit(REDACTED, 1)[0] != redacted[1].rsplit(REDACTED, 1)[0]
    assert all("kkk" not in location for location in redacted)
    assert all(len(location) <= _POINTER_BOUND and _accepts(location) for location in redacted)
    # A redacted pointer is rebuilt through the one escaping function the core writes pointers
    # with, and U+2026 is printable, so the truncation marker comes back as the character
    # itself rather than as the six characters the core writes. It marks the same cut and is
    # still single-line displayable; only its spelling differs once a pointer is rebuilt.
    assert all(_TRUNCATION_MARKER_ESCAPE in finding.location for finding in findings)
    assert all(_TRUNCATION_MARKER_ESCAPE not in location for location in redacted)
    assert all(_TRUNCATION_MARKER_CHARACTER in location for location in redacted)


def test_an_untouched_component_survives_redaction_of_its_neighbour_byte_for_byte() -> None:
    """Decoding and re-encoding is exact, so reassembly does not rewrite a component it kept.

    The key holds a literal "~" followed by a literal "1" and a literal "/" — the pair that
    decodes wrong if "~0" is decoded before "~1", and encodes wrong if "/" is encoded before
    "~". A redaction elsewhere in the pointer forces the reassembly path, which the unchanged
    pointer returned by a no-op pass would otherwise never exercise.
    """
    collected = "alpha1"
    location = f"/configuration/source/settings/{_escaped('a~1b/c')}/{collected}"

    redacted = configs_service.redact_pointer(location, [collected])

    assert redacted.startswith("/configuration/source/settings/a~01b~1c/***")
    assert _accepts(redacted)


# --- Pre-PR correction F1: the write boundary refuses prebuilt package instances --------


_INLINE_CANARY = "canary-inline-secret-0123456789"


class _HostileDeclaredContent(ConfigurationPackage):
    """Validated fields hold only references; the persisted dump smuggles an inline secret."""

    def declared_content(self) -> dict[str, Any]:
        content = super().declared_content()
        content["configuration"]["source"]["settings"]["token"] = _INLINE_CANARY
        return content


def _prebuilt_instances() -> tuple[tuple[str, ConfigurationPackage], ...]:
    return (
        ("exact-class", ConfigurationPackage.model_validate(package_data())),
        ("subclass", _HostileDeclaredContent.model_validate(package_data())),
        ("model-construct", ConfigurationPackage.model_construct(**package_data())),
    )


@pytest.mark.parametrize("instance", [pytest.param(instance, id=name) for name, instance in _prebuilt_instances()])
def test_register_refuses_a_prebuilt_package_instance(tmp_path: Path, instance: ConfigurationPackage) -> None:
    # Any instance — the exact class included — can carry behavior validation never judged:
    # a subclass overriding ``declared_content()``, or ``model_construct`` skipping
    # validation entirely. The boundary accepts declared JSON-native content only.
    location = _store(tmp_path)
    # Typed away deliberately: the whole point is a call the signature no longer admits.
    prebuilt = cast("Any", instance)

    with pytest.raises(configs_service.ConfigsRequestError, match="must be a JSON-native dict"):
        configs_service.register(package=prebuilt, product_cache_location=location)

    assert _registered_configuration_count(tmp_path / "product-cache") == 0


@pytest.mark.parametrize("instance", [pytest.param(instance, id=name) for name, instance in _prebuilt_instances()])
def test_create_version_refuses_a_prebuilt_package_instance(tmp_path: Path, instance: ConfigurationPackage) -> None:
    location = _store(tmp_path)
    registered = configs_service.register(package=package_data(), product_cache_location=location)
    prebuilt = cast("Any", instance)

    with pytest.raises(configs_service.ConfigsRequestError, match="must be a JSON-native dict"):
        configs_service.create_version(
            config_id=registered.configuration.config_id,
            package=prebuilt,
            product_cache_location=location,
        )

    versions = configs_service.list_versions(
        config_id=registered.configuration.config_id, product_cache_location=location
    )
    assert [version.registry_version for version in versions] == [1]


def test_a_hostile_declared_content_override_cannot_reach_the_store(tmp_path: Path) -> None:
    """The pre-PR review reproduction, now failing safe.

    Before the boundary refused instances, this subclass registered successfully and the
    inline canary landed in the durable store row. Now the instance itself is refused as
    the caller's own input, and nothing is written.
    """
    location = _store(tmp_path)
    hostile = cast("Any", _HostileDeclaredContent.model_validate(package_data()))

    with pytest.raises(configs_service.ConfigsRequestError):
        configs_service.register(package=hostile, product_cache_location=location)

    root = tmp_path / "product-cache"
    assert _registered_configuration_count(root) == 0
    database = root / "product-records.sqlite3"
    if database.exists():
        assert _INLINE_CANARY.encode() not in database.read_bytes()


def test_a_non_mapping_package_is_the_callers_input_not_an_internal_error(tmp_path: Path) -> None:
    with pytest.raises(configs_service.ConfigsRequestError, match="package must be a JSON-native dict"):
        configs_service.register(package=_WRONG_TYPED_VALUE, product_cache_location=_store(tmp_path))


# --- Property closure P1: the write boundary accepts exactly JSON-native data -----------


class _MappingSubclassPackage(Mapping):
    """A well-behaved ``Mapping`` that is not an exact ``dict``: outside the domain."""

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def __getitem__(self, key: str) -> object:
        return self._data[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)


class _DictSubclassPackage(dict):  # noqa: FURB189 - the exact-dict boundary is the subject
    """An exact-shape ``dict`` subclass: every protocol hook is overridable behavior."""


class _RaisingClassPackage:
    """A hostile argument whose ``__class__`` raises the moment anything consults it."""

    @property
    def __class__(self) -> type:
        msg = "__class__ was consulted"
        raise RuntimeError(msg)


class _ProtocolRecordingPackage:
    """Records every protocol consultation acceptance could make on the untrusted value."""

    def __init__(self) -> None:
        self.consulted: list[str] = []

    @property
    def __class__(self) -> type:
        self.consulted.append("__class__")
        return _ProtocolRecordingPackage

    def keys(self) -> Iterator[object]:
        self.consulted.append("keys")
        return iter(())

    def items(self) -> Iterator[object]:
        self.consulted.append("items")
        return iter(())

    def __getitem__(self, key: object) -> object:
        self.consulted.append("__getitem__")
        raise KeyError(key)

    def __iter__(self) -> Iterator[object]:
        self.consulted.append("__iter__")
        return iter(())

    def __len__(self) -> int:
        self.consulted.append("__len__")
        return 0

    def __contains__(self, key: object) -> bool:
        self.consulted.append("__contains__")
        return False


def _package_with_leaf(leaf: object) -> dict[str, Any]:
    data = package_data()
    data["configuration"]["source"]["settings"]["marker"] = leaf
    return data


def _non_json_native_packages() -> tuple[tuple[str, object], ...]:
    return (
        ("mapping-subclass", _MappingSubclassPackage(package_data())),
        ("dict-subclass", _DictSubclassPackage(package_data())),
        ("raising-class", _RaisingClassPackage()),
        ("nested-set", _package_with_leaf({"a"})),
        ("nested-decimal", _package_with_leaf(Decimal(1))),
        ("nested-object", _package_with_leaf(object())),
    )


@pytest.mark.parametrize("value", [pytest.param(value, id=name) for name, value in _non_json_native_packages()])
def test_only_recursively_exact_json_data_enters_the_write_boundary(tmp_path: Path, value: object) -> None:
    # The closed acceptance property: a package is either recursively exact JSON-native
    # data — exact dict/list containers, exact str/int/float/bool/None leaves — or it is
    # the caller's own input, refused request-class before any protocol operation runs.
    location = _store(tmp_path)

    with pytest.raises(configs_service.ConfigsRequestError) as raised:
        configs_service.register(package=cast("Any", value), product_cache_location=location)

    assert raised.value.family == "request"
    assert _registered_configuration_count(tmp_path / "product-cache") == 0


def test_an_exact_dict_package_is_the_accepted_domain(tmp_path: Path) -> None:
    registered = configs_service.register(package=package_data(), product_cache_location=_store(tmp_path))

    assert registered.version.registry_version == 1


def test_a_refused_package_is_never_invoked(tmp_path: Path) -> None:
    # Structural acceptance judges type(...) identity alone: the sentinel records every
    # protocol hook acceptance could consult — __class__ included — and none may fire.
    sentinel = _ProtocolRecordingPackage()

    with pytest.raises(configs_service.ConfigsRequestError):
        configs_service.register(package=cast("Any", sentinel), product_cache_location=_store(tmp_path))

    assert sentinel.consulted == []


def test_a_rejected_package_never_has_its_class_metadata_read(tmp_path: Path) -> None:
    # Inspection is invocation, class metadata included: the refusal used to read
    # ``__mro__`` and ``__name__`` off the untrusted value's class, and a metaclass
    # executes on those reads — the probe escaped register() as ConfigsInternalError
    # with the read recorded. The refusal reads nothing: one fixed message.
    reads: list[str] = []

    class _ExecutingMeta(type):
        @property
        def __mro__(cls) -> tuple[type, ...]:  # noqa: PLW3201 - shadowing type's own descriptor is the fixture
            reads.append("__mro__")
            msg = "metaclass executed on __mro__ read"
            raise RuntimeError(msg)

        @property
        def __name__(cls) -> str:  # noqa: PLW3201 - shadowing type's own descriptor is the fixture
            reads.append("__name__")
            msg = "metaclass executed on __name__ read"
            raise RuntimeError(msg)

    class _MetadataProbe(metaclass=_ExecutingMeta):
        """An out-of-domain value whose class metadata is executable behavior."""

    with pytest.raises(configs_service.ConfigsRequestError) as raised:
        configs_service.register(package=cast("Any", _MetadataProbe()), product_cache_location=_store(tmp_path))

    assert raised.value.family == "request"
    assert "must be a JSON-native dict" in str(raised.value)
    assert reads == []
    assert _registered_configuration_count(tmp_path / "product-cache") == 0


def _hostile_metadata_instance() -> tuple[object, list[str]]:
    """An out-of-domain value whose class ``__name__`` read is recorded executable behavior."""
    reads: list[str] = []

    class _ExecutingMeta(type):
        @property
        def __name__(cls) -> str:  # noqa: PLW3201 - shadowing type's own descriptor is the fixture
            reads.append("__name__")
            msg = "metaclass executed on __name__ read"
            raise RuntimeError(msg)

    class _Probe(metaclass=_ExecutingMeta):
        """A caller-supplied value whose class metadata is executable behavior."""

    return _Probe(), reads


@pytest.mark.parametrize(
    "call",
    [
        pytest.param(
            lambda location, value: configs_service.get_config(config_id=value, product_cache_location=location),
            id="config-id",
        ),
        pytest.param(
            lambda location, value: configs_service.get_version(
                config_id="c", registry_version=value, product_cache_location=location
            ),
            id="registry-version",
        ),
        pytest.param(
            lambda location, value: configs_service.validate(
                config_id="c",
                registry_version=1,
                product_cache_location=location,
                destination_schema=value,
            ),
            id="destination-schema",
        ),
    ],
)
def test_a_wrong_typed_argument_never_has_its_class_metadata_read(
    tmp_path: Path, call: Callable[[str, Any], object]
) -> None:
    # The package boundary's rule applied to every argument guard: the refusals
    # formatted type(value).__name__, and a metaclass executes on that read — the
    # probe escaped each public operation as ConfigsInternalError.
    probe, reads = _hostile_metadata_instance()

    with pytest.raises(configs_service.ConfigsRequestError) as raised:
        call(_store(tmp_path), cast("Any", probe))

    assert raised.value.family == "request"
    assert reads == []


def test_a_wrong_typed_argument_with_a_raising_class_property_is_the_callers_input(tmp_path: Path) -> None:
    # isinstance() is itself inspection: it consults the instance's __class__, which a
    # hostile property executes on, so this probe escaped get_config() as
    # ConfigsInternalError. The guard matches type(value) identity alone and reads
    # nothing off the instance.
    reads: list[str] = []

    class _RaisingClassProbe:
        @property
        def __class__(self) -> type:
            reads.append("__class__")
            msg = "__class__ was consulted"
            raise RuntimeError(msg)

    with pytest.raises(configs_service.ConfigsRequestError) as raised:
        configs_service.get_config(config_id=cast("Any", _RaisingClassProbe()), product_cache_location=_store(tmp_path))

    assert raised.value.family == "request"
    assert reads == []


def test_the_boundary_classifies_without_consulting_an_exceptions_class_property(tmp_path: Path) -> None:
    # The boundary's isinstance classification consulted exc.__class__ — an instance
    # read a hostile property executes on — so an exception a hostile caller argument
    # constructs escaped list_configs() as a raw RuntimeError, outside the declared
    # vocabulary. Except clauses match the actual type without touching the instance.
    del tmp_path
    reads: list[str] = []

    class _HostileError(Exception):
        @property
        def __class__(self) -> type:
            reads.append("__class__")
            msg = "__class__ was consulted"
            raise RuntimeError(msg)

    class _RaisingLocation:
        def __str__(self) -> str:
            msg = "str() exploded: third-party secret text"
            raise _HostileError(msg)

    with pytest.raises(configs_service.ConfigsInternalError) as raised:
        configs_service.list_configs(product_cache_location=cast("Any", _RaisingLocation()))

    assert raised.value.family == "internal"
    assert str(raised.value) == "configs list_configs failed"
    assert reads == []


def test_the_boundary_reads_nothing_from_an_exception_it_did_not_name(tmp_path: Path) -> None:
    # The boundary's own refusal formatted type(exc).__name__ — but an exception a
    # hostile caller argument constructs (here: a product_cache_location whose
    # __str__ raises it) has an untrusted class, and the metaclass executing on that
    # read escaped the module as a raw RuntimeError, outside the declared vocabulary
    # entirely. The boundary reads nothing: family by isinstance, one fixed message.
    del tmp_path
    reads: list[str] = []

    class _ExecutingMeta(type):
        @property
        def __name__(cls) -> str:  # noqa: PLW3201 - shadowing type's own descriptor is the fixture
            reads.append("__name__")
            msg = "metaclass executed on __name__ read"
            raise RuntimeError(msg)

    class _HostileError(Exception, metaclass=_ExecutingMeta):
        """An exception whose class name read is executable behavior."""

    class _RaisingLocation:
        def __str__(self) -> str:
            msg = "str() exploded: third-party secret text"
            raise _HostileError(msg)

    with pytest.raises(configs_service.ConfigsInternalError) as raised:
        configs_service.list_configs(product_cache_location=cast("Any", _RaisingLocation()))

    assert raised.value.family == "internal"
    assert str(raised.value) == "configs list_configs failed"
    assert reads == []


# --- Pre-PR correction F4: the registry_version domain is exactly positive int ----------


class _IntSubclass(int):
    """An int subclass: well-behaved arithmetically, still not the declared domain."""


_OUT_OF_DOMAIN_REGISTRY_VERSIONS: tuple[tuple[str, object], ...] = (
    # bool is an int subclass, so an isinstance guard let True reach SQLite, compare
    # equal to 1, and silently resolve version 1 - a caller defect answered as data.
    ("true", True),
    ("false", False),
    # The registry allocates from 1: 0 and -1 are not versions that happen to be absent,
    # so reporting them not-found claimed the store answered an unaskable question.
    ("zero", 0),
    ("negative", -1),
    ("int-subclass", _IntSubclass(1)),
    # The storage domain has an upper edge too: SQLite INTEGER is signed 64-bit, so the
    # registry can never allocate above 2**63 - 1. 2**63 passed the positive-int guard
    # and surfaced as SQLite's own OverflowError - an internal error for caller input.
    ("max-plus-one", 2**63),
    ("far-beyond-the-domain", 2**200),
)


@pytest.mark.parametrize(
    "registry_version", [pytest.param(value, id=name) for name, value in _OUT_OF_DOMAIN_REGISTRY_VERSIONS]
)
@pytest.mark.parametrize("operation", ["get_version", "validate"])
def test_an_out_of_domain_registry_version_is_the_callers_input(
    tmp_path: Path, operation: str, registry_version: object
) -> None:
    location = _store(tmp_path)
    registered = configs_service.register(package=package_data(), product_cache_location=location)
    call = getattr(configs_service, operation)

    with pytest.raises(configs_service.ConfigsRequestError) as raised:
        call(
            config_id=registered.configuration.config_id,
            registry_version=registry_version,
            product_cache_location=location,
        )

    assert raised.value.family == "request"


def test_the_domain_maximum_is_still_the_stores_own_answer(tmp_path: Path) -> None:
    # The property's upper edge from the inside: 2**63 - 1 is the last version the
    # registry could ever allocate, so it reaches the store and is its own not-found.
    location = _store(tmp_path)
    registered = configs_service.register(package=package_data(), product_cache_location=location)

    with pytest.raises(configs_service.ConfigsNotFoundError) as raised:
        configs_service.get_version(
            config_id=registered.configuration.config_id,
            registry_version=2**63 - 1,
            product_cache_location=location,
        )

    assert raised.value.reason == configs_service.CONFIGURATION_VERSION_NOT_FOUND_REASON


def test_a_large_valid_registry_version_is_still_the_stores_own_answer(tmp_path: Path) -> None:
    # The guard checks the domain and nothing else: a well-formed version the registry has
    # not allocated is still absence, reported by the store's own not-found.
    location = _store(tmp_path)
    registered = configs_service.register(package=package_data(), product_cache_location=location)

    with pytest.raises(configs_service.ConfigsNotFoundError) as raised:
        configs_service.get_version(
            config_id=registered.configuration.config_id,
            registry_version=10**12,
            product_cache_location=location,
        )

    assert raised.value.reason == configs_service.CONFIGURATION_VERSION_NOT_FOUND_REASON
