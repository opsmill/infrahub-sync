"""T072 — SC-010: a credential in `settings` reaches no artifact, no review output, no reader value.

FR-018 says no secret value appears in the plan artifact or in any review output, and AD018
fixes both the mechanism and the injection point: credentials enter this system as values in
the configuration's `settings`, and FR-018 is defended by never writing those values out. So
this file injects a **synthetic** canary credential into `settings` — into a configuration
written to disk and loaded through the real loader, not a hand-built object — runs a real
plan, and scans the four surfaces SC-010 enumerates:

1. the artifact files under `<run_dir>/plan/`, read as bytes straight from disk;
2. the CLI's captured standard output at the **summary** depth;
3. the CLI's captured standard output at the **per-object** depth;
4. the in-process reader's returned value, walked **as data**.

The fourth is the one that needs saying. `read_saved_plan` returns a `SavedPlan`, and
scanning `repr(plan)` would scan almost nothing — the default `repr` of that object names its
class and its address. `_data_leaves` therefore walks the object *graph*: pydantic models
through `model_dump()` **and** their tolerated extra fields (`PlanManifest` allows extras per
FR-027, and an extra lives in `__pydantic_extra__` rather than in `__dict__`), frozen
dataclasses through their fields, mappings through keys *and* values, sequences and sets
through their items, and any other object through `vars()` — falling back to `repr` only for
something with no attributes at all. Private attributes are walked too, so `SavedPlan`'s
`_operations` and `_declared_kinds` are scanned rather than trusted. The value the reader
hands back through its public methods, `summary()` and `operations()`, is walked alongside it.

`plan.parquet` is deliberately **not** scanned. FR-019 keeps the pre-existing plan file
in place, never reads it through the new reader, and states that it "is not part of the plan
artifact for FR-004, FR-018, SC-006 or SC-010". Scanning it would assert a claim the spec
does not make.

**The positive controls are the point of the file.** A scan that finds nothing proves nothing
until it is shown finding something, so `test_a_planted_canary_is_caught_*` plants the canary
at four sites and asserts the exact set of surfaces each one reaches — a payload plant, an
identity plant, a destination-kind plant, and a plant in a payload field whose name falls
under the review's redaction policy. Their union is all four surfaces, asserted as its own
case, so no scanned surface is scanned by a matcher that has never fired.

The four reach different surfaces because the two review depths render different things: the
summary depth renders counts keyed by action and kind and nothing per-record, so only a kind
name reaches it, while the detail depth renders each operation's identity **and** — since
FIX-012 (spec 002) — the desired destination state it would write, payload values included.
That is why the redaction-policy plant is here: it lands in the artifact and in the reader's
data like any other payload value, and the detail depth withholds it, so the policy is pinned
by a control that fires rather than by the reading of a constant.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

import pydantic
import pytest
import yaml
from typer.testing import CliRunner

from infrahub_sync.cli import app
from infrahub_sync.plan.config_version import default_config_version
from infrahub_sync.plan.review import read_saved_plan
from infrahub_sync.plan.writer import PLAN_DIR_NAME
from infrahub_sync.utils import get_instance
from tests.plan.artifact_fixtures import RUN_ID
from tests.test_potenda_plan_artifact import (
    KINDS,
    _FakeAdapter,
    _FakeRecord,
    build_potenda,
    mapping_entry,
    qualified_mapping,
    run_plan,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator
    from pathlib import Path

# A **synthetic** value invented for this test. It is not a credential of anything: it exists
# only so a scan has something unambiguous to look for, and it is distinctive enough that a
# substring match cannot collide with ordinary artifact content.
CANARY = "c4n4ry-7f3e9a1d2b48605-do-not-leak"

SYNC_NAME = "canary-sync"

# The three plant sites, and the surface names.
ARTIFACT_FILES = "artifact files"
SUMMARY_STDOUT = "cli summary stdout"
DETAIL_STDOUT = "cli detail stdout"
READER_DATA = "reader data"
SURFACES = (ARTIFACT_FILES, SUMMARY_STDOUT, DETAIL_STDOUT, READER_DATA)

# A destination kind whose *name* carries the canary. Contrived on purpose: it is the only
# thing a summary depth that renders nothing but counts can be made to echo, and a positive
# control for that surface has to reach it somehow.
CANARY_KIND = f"Canary{CANARY}"

# A payload field whose **name** falls under the review's redaction policy (FIX-012), used by
# the plant that proves the policy withholds the value while the artifact still carries it.
REDACTED_FIELD = "api_token"
REDACTED_PLANT = "redacted_payload_field"

runner = CliRunner()


# ======================================================================================
# The scan
# ======================================================================================


def _decode(raw: bytes) -> str:
    """Decode artifact bytes without ever raising, so a scan cannot be skipped by an error."""
    return raw.decode("utf-8", errors="replace")


def _artifact_leaves(plan_directory: Path) -> Iterator[tuple[str, str]]:
    """Yield `(location, text)` for every file in the artifact directory, read as bytes.

    Read from disk rather than through the reader: FR-018 is a claim about what was
    *written*, and a reader that filtered a field on the way back would hide a leak.
    """
    for path in sorted(plan_directory.rglob("*")):
        if path.is_file():
            yield f"{PLAN_DIR_NAME}/{path.relative_to(plan_directory).as_posix()}", _decode(path.read_bytes())


def _data_leaves(  # noqa: PLR0911, PLR0912 — one branch and one exit per kind of value walked
    value: Any,  # noqa: ANN401 — the walker's whole job is to accept any value
    *,
    location: str = "$",
    seen: dict[int, Any] | None = None,
) -> Iterator[tuple[str, str]]:
    """Walk an object graph and yield `(location, text)` for every string it can reach.

    `seen` maps `id()` to the object itself rather than holding bare identifiers: a set of
    identifiers would let a temporary built during the walk be collected and its address
    reused, which silently skips a later object. Holding the reference makes the address
    stable for the walk's lifetime.
    """
    # One branch and one exit per kind of value walked.
    # pylint: disable=too-many-return-statements,too-many-branches
    seen = {} if seen is None else seen
    if isinstance(value, str):
        yield location, value
        return
    if isinstance(value, (bytes, bytearray)):
        yield location, _decode(bytes(value))
        return
    if value is None or isinstance(value, (bool, int, float)):
        yield location, str(value)
        return
    if id(value) in seen:
        return
    seen[id(value)] = value

    if isinstance(value, pydantic.BaseModel):
        # `model_dump()` for the declared fields, `__pydantic_extra__` for the unknown ones
        # `PlanManifest` tolerates and preserves verbatim (FR-027) — those are not in
        # `__dict__`, so a `vars()` walk alone would miss a secret carried in one.
        yield from _data_leaves(value.model_dump(mode="python"), location=f"{location}(fields)", seen=seen)
        extra = getattr(value, "__pydantic_extra__", None)
        if extra:
            yield from _data_leaves(extra, location=f"{location}(extra)", seen=seen)
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            yield from _data_leaves(key, location=f"{location}(key)", seen=seen)
            yield from _data_leaves(item, location=f"{location}[{key!r}]", seen=seen)
        return
    if isinstance(value, (list, tuple, set, frozenset)):
        ordered = sorted(value, key=repr) if isinstance(value, (set, frozenset)) else list(value)
        for index, item in enumerate(ordered):
            yield from _data_leaves(item, location=f"{location}[{index}]", seen=seen)
        return
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        for field in dataclasses.fields(value):
            yield from _data_leaves(getattr(value, field.name), location=f"{location}.{field.name}", seen=seen)
        return
    attributes = getattr(value, "__dict__", None)
    if attributes:
        for name, item in dict(attributes).items():
            yield from _data_leaves(item, location=f"{location}.{name}", seen=seen)
        return
    slots = getattr(type(value), "__slots__", ())
    if slots:
        for name in slots:
            yield from _data_leaves(getattr(value, name, None), location=f"{location}.{name}", seen=seen)
        return
    # Nothing to walk into: an opaque object is scanned as its own text.
    yield location, repr(value)


def _hits(leaves: Iterable[tuple[str, str]]) -> list[str]:
    """The locations at which the canary appears, each with the text that carried it."""
    return [f"{location}: {text.strip()[:200]}" for location, text in leaves if CANARY in text]


# ======================================================================================
# The configuration, the plan run, and the four surfaces
# ======================================================================================


def _canary_settings(url: str) -> dict[str, str]:
    """Adapter settings carrying the canary in the three shapes a credential takes here."""
    return {"url": url, "token": CANARY, "password": CANARY}


def _write_configuration(projects_root: Path, *, extra_kind: str | None = None, extra_field: str | None = None) -> Path:
    """Write a real `config.yml` whose `settings` carry the canary, and return its directory.

    Written to disk and loaded back through `get_instance` rather than constructed in
    memory, because AD018's injection point is *the configuration's* `settings` and the
    loader is what puts them on the object every later stage reads.
    """
    mapping = list(qualified_mapping())
    order = list(KINDS)
    if extra_kind is not None:
        mapping.append(mapping_entry(extra_kind, identifiers=["name"], fields={"name": None}))
        order.append(extra_kind)
    if extra_field is not None:
        # Declared on `BuiltinTag`, the kind the payload plants use, so the planted value is
        # mapped into the plan's payload rather than dropped as an unmapped attribute.
        mapping[0] = mapping_entry(
            "BuiltinTag",
            identifiers=["name"],
            fields={"name": None, "description": None, "slug": None, extra_field: None},
        )
    project = projects_root / "canary-project"
    project.mkdir(parents=True, exist_ok=True)
    document = {
        "name": SYNC_NAME,
        "source": {"name": "netbox", "settings": _canary_settings("https://source.invalid")},
        "destination": {"name": "infrahub", "settings": _canary_settings("https://destination.invalid")},
        "order": order,
        "schema_mapping": [entry.model_dump() for entry in mapping],
    }
    (project / "config.yml").write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return project


def _source_adapter(*, plant: str | None) -> _FakeAdapter:
    """The source side, with the canary planted at `plant` — or nowhere, for the real case."""
    records = [
        _FakeRecord("BuiltinTag", {"name": "prod"}, {"description": "production", "slug": "prod"}),
        _FakeRecord("LocationSite", {"name": "hq"}, {}),
        _FakeRecord("LocationRack", {"name": "r1", "site": "hq"}, {}),
        _FakeRecord("DcimDevice", {"name": "d1", "rack": "r1__hq"}, {"model": "c9300", "tags": []}),
    ]
    if plant == "payload":
        records.append(_FakeRecord("BuiltinTag", {"name": "planted"}, {"description": CANARY, "slug": "planted"}))
    if plant == "identity":
        records.append(_FakeRecord("BuiltinTag", {"name": CANARY}, {"description": "planted", "slug": "planted"}))
    if plant == "kind":
        records.append(_FakeRecord(CANARY_KIND, {"name": "planted"}, {}))
    if plant == REDACTED_PLANT:
        records.append(
            _FakeRecord(
                "BuiltinTag",
                {"name": "planted"},
                {"description": "planted", "slug": "planted", REDACTED_FIELD: CANARY},
            )
        )
    return _FakeAdapter("source", records)


def _destination_adapter() -> _FakeAdapter:
    """A destination holding one object the source does not, so the plan records a delete.

    A delete makes the AD056 disclosure render at both depths, which is more review output
    for the scan to cover — a scan over a plan with nothing to disclose would cover less.
    """
    return _FakeAdapter(
        "destination",
        [_FakeRecord("BuiltinTag", {"name": "stale"}, {"description": "left over", "slug": "stale"})],
    )


def _review(project: Path, *options: str) -> Any:  # noqa: ANN401 — click's Result type is not exported
    """Invoke the review depth selected by `options` and return click's result."""
    return runner.invoke(
        app,
        ["diff", "--name", SYNC_NAME, "--directory", str(project.parent), "--from-plan", RUN_ID, *options],
    )


def _plan_and_scan(projects_root: Path, *, plant: str | None = None) -> dict[str, list[str]]:
    """Run one plan against a canary-bearing configuration and scan all four surfaces.

    Returns the canary's hits per surface — empty lists for the SC-010 case, and the
    positive controls' expected non-empty ones.
    """
    project = _write_configuration(
        projects_root,
        extra_kind=CANARY_KIND if plant == "kind" else None,
        extra_field=REDACTED_FIELD if plant == REDACTED_PLANT else None,
    )
    instance = get_instance(name=SYNC_NAME, directory=str(project.parent))
    assert instance is not None, "the canary configuration did not load"
    # The injection is asserted rather than assumed: a configuration that lost the canary
    # would make every assertion below pass while testing nothing (SC-010).
    assert instance.source.settings is not None
    assert instance.destination.settings is not None
    assert CANARY in instance.source.settings.values()
    assert CANARY in instance.destination.settings.values()

    top_level = [*KINDS, CANARY_KIND] if plant == "kind" else list(KINDS)
    potenda = build_potenda(
        config=instance,
        source=_source_adapter(plant=plant),
        destination=_destination_adapter(),
        run_id=RUN_ID,
        top_level=top_level,
    )
    run_plan(potenda)

    run_directory = potenda.run_dir
    assert run_directory is not None
    plan_directory = run_directory / PLAN_DIR_NAME
    assert plan_directory.is_dir(), "the plan run wrote no artifact"

    summary_result = _review(project)
    assert summary_result.exit_code == 0, summary_result.output
    detail_result = _review(project, "--detail")
    assert detail_result.exit_code == 0, detail_result.output

    plan = read_saved_plan(sync_name=SYNC_NAME, run_id=RUN_ID, config=instance)
    assert plan.operations(), "the plan holds no operations, so the scan would cover nothing"

    return {
        ARTIFACT_FILES: _hits(_artifact_leaves(plan_directory)),
        SUMMARY_STDOUT: _hits([("stdout", summary_result.output)]),
        DETAIL_STDOUT: _hits([("stdout", detail_result.output)]),
        READER_DATA: _hits(
            [
                *_data_leaves(plan, location="$plan"),
                *_data_leaves(plan.summary(), location="$summary"),
                *_data_leaves(plan.operations(), location="$operations"),
            ]
        ),
    }


@pytest.fixture(autouse=True)
def _isolated_cache_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the run directory inside the test's own tree, never a developer's cache."""
    monkeypatch.setenv("INFRAHUB_SYNC_CACHE_DIR", str(tmp_path / "cache"))


@pytest.fixture(autouse=True)
def _no_adapter_construction(monkeypatch: pytest.MonkeyPatch) -> None:
    """Refuse adapter construction, so the review depths are reached with both sides absent.

    The plan run above drives fake adapters directly; nothing on the review path may reach
    `import_adapter`, and the honest way to hold that is to make the attempt fail.
    """

    def _refuse(**_kwargs: Any) -> Any:  # noqa: ANN401 — mirrors the real keyword-only signature
        msg = "no adapter may be constructed while reviewing a saved plan (FR-008)"
        raise AssertionError(msg)

    monkeypatch.setattr("infrahub_sync.utils.import_adapter", _refuse)


@pytest.fixture(name="projects_root")
def fixture_projects_root(tmp_path: Path) -> Path:
    """The directory the `--directory` option points at."""
    root = tmp_path / "sync-projects"
    root.mkdir(parents=True, exist_ok=True)
    return root


# ======================================================================================
# SC-010 — the canary appears nowhere
# ======================================================================================


def test_a_settings_credential_reaches_none_of_the_four_scanned_surfaces(projects_root: Path) -> None:
    """SC-010: the artifact, both review depths and the reader's data are all canary-free."""
    hits = _plan_and_scan(projects_root)

    assert hits == {surface: [] for surface in SURFACES}


def test_the_manifest_binds_the_canary_bearing_settings_without_disclosing_them(projects_root: Path) -> None:
    """`config_version` is a digest **over** `settings`, which is exactly why it is safe (AD041)."""
    _write_configuration(projects_root)
    instance = get_instance(name=SYNC_NAME, directory=str(projects_root))
    assert instance is not None

    without_canary = instance.model_copy(deep=True)
    assert without_canary.source.settings is not None
    without_canary.source.settings["token"] = "a-different-value"  # noqa: S105 — a literal, not a credential

    computed = default_config_version(instance)
    assert CANARY not in computed
    assert computed != default_config_version(without_canary), (
        "the configuration version ignores `settings`, so it does not bind the credential at all"
    )


# ======================================================================================
# The positive controls — the scan has teeth
# ======================================================================================

PLANTED_CASES: dict[str, frozenset[str]] = {
    # A canary in an operation's payload. It reaches the artifact, the reader's data and — since
    # FIX-012 renders the desired destination state — the per-object depth. The summary depth
    # renders nothing per-record, which is why it stops short of that one.
    "payload": frozenset({ARTIFACT_FILES, DETAIL_STDOUT, READER_DATA}),
    # An identity value reaches the same three, through the identity the record line renders.
    "identity": frozenset({ARTIFACT_FILES, DETAIL_STDOUT, READER_DATA}),
    # A destination kind reaches all four: the summary depth renders a count per kind.
    "kind": frozenset({ARTIFACT_FILES, SUMMARY_STDOUT, DETAIL_STDOUT, READER_DATA}),
    # The same payload plant in a field the redaction policy covers, which is why it reaches
    # every surface the plant above does **except** the rendered one: the artifact carries the
    # value, and the review withholds it. The negative half of the case above, and the control
    # that would fire if the policy stopped applying.
    REDACTED_PLANT: frozenset({ARTIFACT_FILES, READER_DATA}),
}


@pytest.mark.parametrize(("plant", "expected"), list(PLANTED_CASES.items()), ids=list(PLANTED_CASES))
def test_a_planted_canary_is_caught_on_exactly_the_surfaces_it_reaches(
    projects_root: Path,
    plant: str,
    expected: frozenset[str],
) -> None:
    """Each plant is found wherever it lands, and reported nowhere it does not."""
    hits = _plan_and_scan(projects_root, plant=plant)

    reached = {surface for surface, found in hits.items() if found}
    assert reached == set(expected), f"planting in the {plant} reached {sorted(reached)}"
    for surface in expected:
        assert hits[surface], f"the {surface} scan did not catch a canary planted in the {plant}"


def test_every_scanned_surface_has_a_plant_that_proves_it() -> None:
    """No surface is scanned by a matcher that has never been shown to fire."""
    proven: set[str] = set()
    for surfaces in PLANTED_CASES.values():
        proven |= set(surfaces)

    assert proven == set(SURFACES)
