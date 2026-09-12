"""T014 — the configuration-version value a saved plan is bound to (FR-011, AD041, PD-003).

Two consequences the decision states explicitly are asserted here rather than left to
review: `directory` is **out**, so a plan produced in CI is applicable from a developer's
checkout; and `settings` is **in**, so rotating a credential invalidates every saved plan
for that configuration. Both are properties an operator can be surprised by, so both get a
named test.

A caller-supplied value is opaque: it round-trips **verbatim** and is never parsed (AD013).

**T057** adds SC-013's apply side at the end of the file: the same opaqueness measured
end to end, through the real manifest write and the real apply-time comparison, rather than
against `validate_config_version` alone. Paired with SC-004's mismatch case (T025), the
criterion is complete.
"""

from __future__ import annotations

import copy
import hashlib
import json
import string
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
import yaml

from infrahub_sync import SyncInstance
from infrahub_sync.plan.canonical import canonical_json_bytes
from infrahub_sync.plan.config_version import (
    CONFIG_VERSION_EXCLUDED_FIELDS,
    default_config_version,
    resolve_config_version,
    validate_config_version,
)
from infrahub_sync.plan.errors import PlanVerificationError
from infrahub_sync.plan.models import PlannedOperation
from infrahub_sync.plan.reader import load_plan_artifact
from infrahub_sync.plan.writer import MANIFEST_FILE_NAME, PLAN_DIR_NAME, write_plan_artifact
from infrahub_sync.potenda import Potenda
from infrahub_sync.utils import get_instance
from tests.plan.artifact_fixtures import RUN_ID, operation_record
from tests.plan.ownership_fixtures import granted_ownership

if TYPE_CHECKING:
    from pathlib import Path

CONFIG_DATA: dict[str, Any] = {
    "name": "demo",
    "source": {"name": "netbox", "settings": {"url": "http://netbox.local", "token": "nb-token"}},
    "destination": {"name": "infrahub", "settings": {"url": "http://infrahub.local", "token": "ih-token"}},
    "order": ["BuiltinTag"],
    "schema_mapping": [
        {
            "name": "BuiltinTag",
            "mapping": "extras.tags",
            "identifiers": ["name"],
            "fields": [{"name": "name", "mapping": "name"}],
        }
    ],
}


def _instance(directory: str = "/repo/examples/demo", **overrides: Any) -> SyncInstance:  # noqa: ANN401 — config blocks are heterogeneous
    """Build a `SyncInstance` from `CONFIG_DATA` with `overrides` deep-merged in."""
    data = copy.deepcopy(CONFIG_DATA)
    data.update(copy.deepcopy(overrides))
    return SyncInstance(**data, directory=directory)


# --------------------------------------------------------------------------------------
# Determinism.
# --------------------------------------------------------------------------------------


def test_determinism_across_two_loads_of_the_same_file(tmp_path: Path) -> None:
    """Two independent loads of one config file produce the same value."""
    config_file = tmp_path / "config.yml"
    config_file.write_text(yaml.safe_dump(CONFIG_DATA), encoding="utf-8")

    first = get_instance(config_file=str(config_file))
    second = get_instance(config_file=str(config_file))

    assert first is not None
    assert second is not None
    assert first is not second
    assert default_config_version(first) == default_config_version(second)


def test_determinism_is_insensitive_to_yaml_comments_and_key_order(tmp_path: Path) -> None:
    """The value covers the configuration **as parsed**, not the file's bytes (PD-003)."""
    plain = tmp_path / "plain" / "config.yml"
    plain.parent.mkdir(parents=True)
    plain.write_text(yaml.safe_dump(CONFIG_DATA, sort_keys=True), encoding="utf-8")

    reordered = tmp_path / "reordered" / "config.yml"
    reordered.parent.mkdir(parents=True)
    reordered.write_text(
        "# a leading comment the digest must not see\n" + yaml.safe_dump(CONFIG_DATA, sort_keys=False) + "\n\n",
        encoding="utf-8",
    )

    assert plain.read_bytes() != reordered.read_bytes()

    left = get_instance(config_file=str(plain))
    right = get_instance(config_file=str(reordered))
    assert left is not None
    assert right is not None
    assert default_config_version(left) == default_config_version(right)


def test_default_matches_the_spelled_out_rule() -> None:
    """sha256 over the canonical JSON of the parsed config with `directory` excluded."""
    instance = _instance()
    expected = hashlib.sha256(
        canonical_json_bytes(instance.model_dump(mode="json", exclude=set(CONFIG_VERSION_EXCLUDED_FIELDS)))
    ).hexdigest()
    assert default_config_version(instance) == expected


def test_default_is_lowercase_hex() -> None:
    """The default value is a bare lowercase sha256 hex digest, no prefix."""
    produced = default_config_version(_instance())
    assert len(produced) == 64
    assert produced == produced.lower()
    assert all(character in "0123456789abcdef" for character in produced)


# --------------------------------------------------------------------------------------
# `directory` excluded; `settings` included.
# --------------------------------------------------------------------------------------


def test_configs_differing_only_in_directory_produce_the_same_value() -> None:
    """`directory` is location, not configuration (PD-003, AD041)."""
    in_ci = _instance(directory="/home/runner/work/repo/examples/demo")
    on_laptop = _instance(directory="/Users/dev/repos/repo/examples/demo")

    assert in_ci.directory != on_laptop.directory
    assert default_config_version(in_ci) == default_config_version(on_laptop)


def test_excluded_fields_are_exactly_directory() -> None:
    """Only `directory` is excluded; nothing else is silently outside the digest."""
    assert set(CONFIG_VERSION_EXCLUDED_FIELDS) == {"directory"}


def test_configs_differing_in_settings_produce_different_values() -> None:
    """`settings` is included: a changed destination address is a changed configuration."""
    baseline = _instance()
    moved = _instance(destination={"name": "infrahub", "settings": {"url": "http://other.local", "token": "ih-token"}})
    assert default_config_version(moved) != default_config_version(baseline)


def test_rotating_a_credential_invalidates_the_value() -> None:
    """The stated consequence of AD041, asserted so it cannot regress unnoticed."""
    before = _instance()
    after = _instance(
        destination={"name": "infrahub", "settings": {"url": "http://infrahub.local", "token": "rotated"}}
    )
    assert default_config_version(after) != default_config_version(before)


def test_no_credential_is_disclosed_by_the_value() -> None:
    """Only the one-way digest is written, so the token never appears in it (FR-018)."""
    produced = default_config_version(_instance())
    assert "ih-token" not in produced
    assert "nb-token" not in produced


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"name": "other"}, id="name"),
        pytest.param({"order": ["LocationSite"]}, id="order"),
        pytest.param(
            {"source": {"name": "nautobot", "settings": {"url": "http://netbox.local", "token": "nb-token"}}},
            id="source adapter",
        ),
        pytest.param(
            {
                "schema_mapping": [
                    {
                        "name": "BuiltinTag",
                        "mapping": "extras.tags",
                        "identifiers": ["name"],
                        "fields": [{"name": "name", "mapping": "slug"}],
                    }
                ]
            },
            id="schema mapping",
        ),
        pytest.param({"incremental": {"full_resync_every": 3}}, id="incremental block"),
    ],
)
def test_any_declared_content_change_changes_the_value(overrides: dict[str, Any]) -> None:
    """Everything the configuration declares, other than `directory`, is inside the digest."""
    assert default_config_version(_instance(**overrides)) != default_config_version(_instance())


# --------------------------------------------------------------------------------------
# The caller-supplied path: verbatim, never parsed.
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "supplied",
    [
        pytest.param("v1", id="short label"),
        pytest.param("git-sha:1a2b3c4d", id="looks like a git sha"),
        pytest.param("a" * 64, id="looks like a sha256 digest"),
        pytest.param('{"not":"parsed"}', id="looks like JSON"),
        pytest.param("release 2.1 (rc-3)", id="spaces and punctuation"),
        pytest.param("  padded  ", id="leading and trailing spaces are preserved"),
        pytest.param(string.punctuation, id="every printable punctuation character"),
        pytest.param(" ", id="a single space is printable ASCII"),
        pytest.param("~", id="the top of the printable range"),
    ],
)
def test_supplied_value_round_trips_verbatim(supplied: str) -> None:
    """The value is stored byte for byte, unparsed, untrimmed, uninterpreted (AD013)."""
    assert validate_config_version(supplied) == supplied
    assert resolve_config_version(_instance(), supplied) == supplied


def test_supplied_value_replaces_the_default_entirely() -> None:
    """A supplied value is not mixed with, or digested alongside, the default rule's value."""
    instance = _instance()
    supplied = "release-2.1"
    assert resolve_config_version(instance, supplied) == supplied
    assert resolve_config_version(instance, supplied) != default_config_version(instance)


def test_no_supplied_value_falls_back_to_the_default_rule() -> None:
    """`None` means "compute it", which is the plan run's ordinary path."""
    instance = _instance()
    assert resolve_config_version(instance) == default_config_version(instance)
    assert resolve_config_version(instance, None) == default_config_version(instance)


def test_a_supplied_value_is_insensitive_to_the_configuration() -> None:
    """Proof it is not parsed or blended: two different configs keep the same value."""
    supplied = "release-2.1"
    assert resolve_config_version(_instance(), supplied) == resolve_config_version(_instance(name="other"), supplied)


@pytest.mark.parametrize(
    "supplied",
    [
        pytest.param("", id="empty"),
        pytest.param("\n", id="bare LF"),
        pytest.param("v1\n", id="trailing LF — would break the manifest line"),
        pytest.param("v1\r\n", id="trailing CRLF"),
        pytest.param("v1\tv2", id="embedded tab"),
        pytest.param("v1\x00", id="embedded NUL"),
        pytest.param("café", id="non-ASCII"),
        pytest.param("v\x7f", id="DEL, just above the printable range"),
        pytest.param("v\x1f", id="US, just below the printable range"),
    ],
)
def test_empty_and_non_printable_supplied_values_are_rejected(supplied: str) -> None:
    """A value that cannot survive the manifest as one printable ASCII line is refused."""
    with pytest.raises(ValueError):
        validate_config_version(supplied)
    with pytest.raises(ValueError):
        resolve_config_version(_instance(), supplied)


def test_a_trailing_newline_is_rejected_rather_than_matched_by_dollar() -> None:
    """`re.fullmatch` must consume the whole string, so `$`'s before-LF quirk cannot apply."""
    with pytest.raises(ValueError):
        validate_config_version("v1\n")
    assert validate_config_version("v1") == "v1"


# --------------------------------------------------------------------------------------
# SC-013's apply side: through the manifest write, compared verbatim at apply.
# --------------------------------------------------------------------------------------

# Deliberately opaque, and deliberately hostile to every way a value like this gets
# "helpfully" interpreted: leading and trailing spaces a trimmer eats, a leading zero a
# version parser normalises away, doubled inner spaces a whitespace-collapser folds, mixed
# case a case-folder flattens, and punctuation a tokenizer splits on. Every character is
# printable ASCII, so it survives the manifest as one line.
OPAQUE_VERSION = ' v1.02+Build.7  (rc-2) [beta] {"k": "v"} '

# Each is what one of those interpretations would turn `OPAQUE_VERSION` into. Every one of
# them must be **refused**, because an equality comparison is the whole contract: the value
# is opaque, so the tool has no basis for deciding that any of these means the same thing.
NORMALIZED_FORMS = (
    pytest.param(OPAQUE_VERSION.strip(), id="trimmed"),
    pytest.param(" ".join(OPAQUE_VERSION.split()), id="whitespace collapsed"),
    pytest.param(OPAQUE_VERSION.lower(), id="case folded"),
    pytest.param(OPAQUE_VERSION.replace("1.02", "1.2"), id="version-parsed: the leading zero dropped"),
)


class NoOpPlannedWriteDestination:
    """A destination with the planned-write surface that records what it was asked to write.

    Both protocol members, because the pre-write gate is an `isinstance` check against the
    write-surface protocol and a destination missing either one is refused (AD086).
    """

    def __init__(self) -> None:
        self.dispatched: list[str] = []

    def new_peer_resolver(self) -> object:  # noqa: PLR6301
        """The per-apply resolver factory; nothing below this double's surface reads it."""
        return object()

    def apply_planned_operation(self, *, operation: PlannedOperation, peers: Any) -> str:  # noqa: ANN401
        _ = peers
        self.dispatched.append(operation.operation_id)
        return "node-1"


def _store_plan(run_directory: Path, *, config_version: str) -> str:
    """Write a one-operation artifact through the real writer and return its stored version.

    The **manifest write** is the round trip SC-013 is about, so the artifact is produced by
    `write_plan_artifact` rather than assembled by hand: the value passes through the model,
    the canonical JSON encoder and the checksum on its way to disk, which is where a
    normalization would creep in unnoticed.
    """
    manifest = write_plan_artifact(
        run_dir=run_directory,
        run_id=RUN_ID,
        config_version=validate_config_version(config_version),
        source_snapshot=[],
        deletes_computed=True,
        operations=[PlannedOperation.model_validate(operation_record())],
    )
    return manifest.config_version


def _apply_with(run_directory: Path, *, config_version: str) -> tuple[str, NoOpPlannedWriteDestination]:
    """Apply the stored plan with `config_version` and return the run state and destination.

    The state rule is the CLI's (`infrahub_sync/cli.py`): `applied` when `apply_plan`
    returns, `failed` when it raises.
    """
    destination = NoOpPlannedWriteDestination()
    engine = Potenda(
        source=SimpleNamespace(top_level=[]),  # ty: ignore[invalid-argument-type]
        destination=destination,  # ty: ignore[invalid-argument-type]
        config=None,  # ty: ignore[invalid-argument-type]
        top_level=["BuiltinTag"],
        run_dir=run_directory,
        run_id=RUN_ID,
    )
    try:
        engine.apply_plan(ownership=granted_ownership(), config_version=config_version)
    except PlanVerificationError:
        return "failed", destination
    return "applied", destination


def test_an_opaque_supplied_version_round_trips_the_manifest_and_applies_verbatim(tmp_path: Path) -> None:
    """SC-013: supplied by an in-process caller, stored byte for byte, matched by equality."""
    directory = tmp_path / RUN_ID
    directory.mkdir(parents=True)

    stored = _store_plan(directory, config_version=OPAQUE_VERSION)

    assert stored == OPAQUE_VERSION, "The manifest write must not trim, fold or reformat the value."
    manifest_bytes = (directory / PLAN_DIR_NAME / MANIFEST_FILE_NAME).read_bytes()
    assert json.dumps(OPAQUE_VERSION).encode() in manifest_bytes, (
        f"The value must appear in the artifact's bytes exactly as supplied. Manifest:\n{manifest_bytes.decode()}"
    )
    assert load_plan_artifact(directory).manifest.config_version == OPAQUE_VERSION

    state, destination = _apply_with(directory, config_version=OPAQUE_VERSION)

    assert state == "applied", "The identical value must compare equal and let the apply proceed."
    assert len(destination.dispatched) == 1


@pytest.mark.parametrize("comparison", NORMALIZED_FORMS)
def test_a_normalized_form_of_the_stored_version_is_refused(tmp_path: Path, comparison: str) -> None:
    """FR-011/AD013: the comparison is equality on the raw value, never a parse."""
    directory = tmp_path / RUN_ID
    directory.mkdir(parents=True)
    _store_plan(directory, config_version=OPAQUE_VERSION)
    assert comparison != OPAQUE_VERSION, "the parameter must differ from the stored value"

    state, destination = _apply_with(directory, config_version=comparison)

    assert state == "failed", f"{comparison!r} is not the stored value and must not be treated as it."
    assert destination.dispatched == [], "Nothing may be written once the configuration version disagrees."
