"""What the clean-host integrity comparisons distinguish, by running them.

Two of the gate's claims are comparisons rather than shapes, and a comparison
that is only read cannot be shown to tell anything apart. Both are reachable
from here without the host the gate runs on:

* the bundle-identity step is a shell function, lifted out of the driver
  verbatim and run in a real `sh` against archives this suite writes;
* the durable-state snapshot's renderings are ordinary functions over the rows
  and bodies the stores hand over, so they are driven with those directly.

Verbatim and directly are the point. What passes here is the fixture's own code,
never a paraphrase of it, and the scaffolding supplied is only what the lifted
code refers to without deciding: a candidate directory, and the driver's own
reporting primitives.
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess  # noqa: S404 - this suite exists to run the driver's own shell
import sys
from collections.abc import Iterable, Iterator, Mapping
from hashlib import sha256
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from typing_extensions import Self

REPO_ROOT = Path(__file__).resolve().parents[2]
DRIVER = REPO_ROOT / "tests" / "compose" / "clean_host" / "clean-host.sh"
CHECKS = REPO_ROOT / "tests" / "compose" / "clean_host" / "checks"

# The archive a candidate directory holds, named the way the record names it.
ARCHIVE = "infrahub-sync-compose-3.0.0.tar.gz"
SHIPPED = b"the bundle this candidate actually ships\n"
SHIPPED_DIGEST = sha256(SHIPPED).hexdigest()
# A digest of bytes no candidate directory below holds, so a record naming it is
# a record that does not name the archive it travels with.
FOREIGN_DIGEST = sha256(b"a bundle this candidate does not ship\n").hexdigest()


def shell_function(name: str) -> str:
    """Return one shell function's whole text, exactly as the driver declares it."""
    source = DRIVER.read_text(encoding="utf-8")
    declared = f"{name}() {{"
    assert declared in source, f"the driver declares no {name}"
    opened = source.index(declared)
    return source[opened : source.index("\n}", opened) + len("\n}")]


def run_shell(script: str, *, work: Path) -> subprocess.CompletedProcess[str]:
    """Run one assembled script under `sh`, from a directory of this suite's own."""
    return subprocess.run(  # noqa: S603 - a script this test assembled, under the shell the driver targets
        ["/bin/sh", "-c", script],
        capture_output=True,
        text=True,
        timeout=30.0,
        cwd=work,
        check=False,
    )


# ---------------------------------------------------------------------------
# Row 1's bundle identity: the archive against the record, not only against the
# checksum file beside it
# ---------------------------------------------------------------------------
def candidate_directory(work: Path, *, recorded: str | None) -> Path:
    """A candidate directory holding an archive, its own checksum file, and a record.

    The checksum file always agrees with the archive: this is the case the gate
    was accepting, so the two have to be a matching pair for the comparison
    under test to be the only thing that can refuse. `recorded` is what the
    record says the bundle's digest is, or nothing at all.
    """
    candidate = work / "candidate"
    candidate.mkdir()
    (candidate / ARCHIVE).write_bytes(SHIPPED)
    (candidate / f"{ARCHIVE}.sha256").write_text(f"{SHIPPED_DIGEST}  {ARCHIVE}\n", encoding="utf-8")
    bundle: dict[str, str] = {"name": ARCHIVE}
    if recorded is not None:
        bundle["sha256"] = recorded
    (candidate / "qualification.json").write_text(json.dumps({"bundle": bundle}), encoding="utf-8")
    return candidate


def bundle_identity_script(candidate: Path) -> str:
    """The driver's own bundle-identity step, with what it does not decide stubbed.

    `record` runs the reader inside the candidate image, which this suite has no
    daemon for. The stub answers one key and refuses every other, so a step that
    read the record's bundle *name* where it should read its digest fails here
    rather than comparing a name against a digest and calling them different.
    """
    return "\n".join(
        [
            "set -eu",
            "ROW=artifact_identity",
            f"CANDIDATE={candidate}",
            shell_function("fail"),
            shell_function("report"),
            shell_function("require"),
            "record() {",
            '    case "$1" in',
            # The reader exits non-zero for a key the record does not hold, which
            # is the shape the lifted step's own `|| fail` is written against.
            "        \"['bundle']['sha256']\")",
            '            sed -n \'s/.*"sha256": "\\([^"]*\\)".*/\\1/p\' "$CANDIDATE/qualification.json" | grep .',
            "            ;;",
            '        *) echo "the harness was asked for $1" >&2; return 1 ;;',
            "    esac",
            "}",
            shell_function("require_recorded_bundle_digest"),
            f'require_recorded_bundle_digest "{ARCHIVE}"',
        ]
    )


def test_an_archive_the_record_does_not_name_is_refused_though_its_checksum_file_agrees(tmp_path: Path) -> None:
    """The accompanying checksum file travels with the archive it describes.

    An archive swapped together with its own `.sha256` satisfies `sha256sum -c`
    and says nothing about the bundle the qualification record names -- and that
    record is the document every other row 1 claim is read from.
    """
    candidate = candidate_directory(tmp_path, recorded=FOREIGN_DIGEST)
    agreed = run_shell(f'cd "{candidate}" && sha256sum -c "{ARCHIVE}.sha256"', work=tmp_path)
    assert agreed.returncode == 0, f"the archive does not agree with the file beside it: {agreed.stderr}"

    answered = run_shell(bundle_identity_script(candidate), work=tmp_path)

    assert answered.returncode != 0, "an archive the record does not name was accepted"
    assert "own digest is not the one the record names" in answered.stderr


def test_an_archive_the_record_names_is_accepted(tmp_path: Path) -> None:
    """A comparison nothing can pass is not a comparison."""
    candidate = candidate_directory(tmp_path, recorded=SHIPPED_DIGEST)

    answered = run_shell(bundle_identity_script(candidate), work=tmp_path)

    assert answered.returncode == 0, answered.stderr
    assert "own digest is the one the record names" in answered.stdout


def test_a_record_that_names_no_digest_ends_the_row(tmp_path: Path) -> None:
    """An absent digest is a record this gate cannot bind the archive to at all.

    Told apart from a mismatch by its own sentence: the two have different causes
    and send a reader to different places.
    """
    candidate = candidate_directory(tmp_path, recorded=None)

    answered = run_shell(bundle_identity_script(candidate), work=tmp_path)

    assert answered.returncode != 0, "a record naming no digest was accepted"
    assert "names no digest for the deployment bundle" in answered.stderr
    assert re.search(r"^clean-host: artifact_identity: ", answered.stderr, re.MULTILINE)


# ---------------------------------------------------------------------------
# The durable-state snapshot: what an operation this gate brackets must leave
# equal, and what a lost-state bug can change while leaving counts and keys
# alone
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def durable_state() -> Iterator[ModuleType]:
    """Load the check the way its own container does, with the kit importable beside it.

    Its two renderings are module level; the stores it reads are reached from
    `main`, which nothing here calls. That is what lets this suite drive the
    renderings where the optional service dependencies are not installed.
    """
    sys.path.insert(0, str(CHECKS))
    try:
        specification = importlib.util.spec_from_file_location("clean_host_durable_state", CHECKS / "durable_state.py")
        assert specification is not None
        loader = specification.loader
        assert loader is not None
        module = importlib.util.module_from_spec(specification)
        loader.exec_module(module)
        yield module
    finally:
        sys.path.remove(str(CHECKS))


# One deployment's durable state, as the two stores hand it over: rows in the
# order a server happened to return them, and object bodies as read chunks.
# `None` is a null column, and the rest are the identifiers and closed-set
# values the product's own tables hold.
ROWS: dict[str, list[tuple[object, ...]]] = {
    "configuration_versions": [("c-1", 1, "checksum-abcd", '{"name": "qualification"}', "2026-09-07T13:00:00+00:00")],
    "product_runs": [
        ("20260907T1307-a09b8278", "plan", "succeeded", None, True),
        ("20260907T1401-b1740c33", "sync", "succeeded", "ab", False),
    ],
}
OBJECTS: dict[str, list[bytes]] = {
    "runs/20260907T1307-a09b8278/plan.json": [b'{"operations": [', b'{"kind": "create"}]}'],
    "runs/20260907T1401-b1740c33/manifest.json": [b'{"checksum": "abcd"}'],
}


def snapshot(
    durable_state: ModuleType, rows: Mapping[str, list[tuple[object, ...]]], objects: Mapping[str, list[bytes]]
) -> str:
    """The whole snapshot the check prints, built from what the two stores would hand over."""
    lines: list[str] = []
    for table in sorted(rows):
        lines.extend(durable_state.table_lines(table, rows[table]))
    lines.extend(durable_state.object_line(key, objects[key]) for key in sorted(objects))
    return "\n".join(lines)


# Each case is a change a deployment that lost durable state can make while
# leaving every count and every key exactly where a weaker snapshot compares
# them. The snapshot has to move for all of them.
PRESERVING_CHANGES: dict[str, tuple[dict[str, list[tuple[object, ...]]], dict[str, list[bytes]]]] = {
    "a stable record replaced with a fresh one of its own": (
        {
            **ROWS,
            "product_runs": [("20260907T1307-ffffffff", "plan", "succeeded", None, True), ROWS["product_runs"][1]],
        },
        OBJECTS,
    ),
    "one column's value replaced, the row count untouched": (
        {**ROWS, "product_runs": [ROWS["product_runs"][0], ("20260907T1401-b1740c33", "sync", "failed", "ab", False)]},
        OBJECTS,
    ),
    "a character moved across a column boundary": (
        {
            **ROWS,
            "product_runs": [ROWS["product_runs"][0], ("20260907T1401-b1740c33", "syncab", "succeeded", "", False)],
        },
        OBJECTS,
    ),
    "a null column now holding the text that spells it": (
        {
            **ROWS,
            "product_runs": [("20260907T1307-a09b8278", "plan", "succeeded", "None", True), ROWS["product_runs"][1]],
        },
        OBJECTS,
    ),
    # A null column and an empty one are different records, and a rendering that
    # only delimited lengths would give both of them nothing to delimit.
    "a null column now holding the empty string": (
        {**ROWS, "product_runs": [("20260907T1307-a09b8278", "plan", "succeeded", "", True), ROWS["product_runs"][1]]},
        OBJECTS,
    ),
    # A null and its neighbour swapping places is what a value written into the
    # wrong column looks like. Tagging the values is not enough to see it: with
    # a null rendering to nothing, the two rows concatenate to the same bytes,
    # and only the length delimiter tells them apart.
    "a null column and its neighbour swapped places": (
        {
            **ROWS,
            "product_runs": [("20260907T1307-a09b8278", "plan", None, "succeeded", True), ROWS["product_runs"][1]],
        },
        OBJECTS,
    ),
    "a record lost": ({**ROWS, "product_runs": ROWS["product_runs"][:1]}, OBJECTS),
    "a whole table lost": (
        {table: rows for table, rows in ROWS.items() if table != "configuration_versions"},
        OBJECTS,
    ),
    "an object body replaced under the key it already had": (
        ROWS,
        {**OBJECTS, "runs/20260907T1401-b1740c33/manifest.json": [b'{"checksum": "beef"}']},
    ),
    "an object body replaced with the same number of bytes": (
        ROWS,
        {**OBJECTS, "runs/20260907T1307-a09b8278/plan.json": [b'{"operations": [', b'{"kind": "delete"}]}']},
    ),
    "an object lost": (
        ROWS,
        {key: body for key, body in OBJECTS.items() if key != "runs/20260907T1401-b1740c33/manifest.json"},
    ),
}


@pytest.mark.parametrize("change", sorted(PRESERVING_CHANGES))
def test_state_a_weaker_snapshot_would_call_equal_moves_the_snapshot(durable_state: ModuleType, change: str) -> None:
    """Counts and keys are what a lost-state bug is most likely to preserve.

    A restart that replaced every record with a fresh one of its own, or rewrote
    an object under the key it already had, keeps every count and every key. So
    the snapshot digests contents, and each case below is a change it has to see.
    """
    rows, objects = PRESERVING_CHANGES[change]

    assert snapshot(durable_state, rows, objects) != snapshot(durable_state, ROWS, OBJECTS), (
        f"{change} leaves the snapshot equal"
    )


# The bytes of one object, and every way a store's stream might hand them over.
# `StreamingBody.read(n)` returns *at most* n bytes: urllib3 reads what the
# socket and the decoder give it, so the boundaries belong to the transfer and
# not to the object.
BODY = b'{"checksum": "abcd", "operations": 3}'
CHUNKINGS: dict[str, list[bytes]] = {
    "one read": [BODY],
    "two reads": [BODY[:9], BODY[9:]],
    "an empty read in the middle": [BODY[:9], b"", BODY[9:]],
    "many uneven reads": [BODY[:1], BODY[1:17], BODY[17:18], BODY[18:]],
    "a byte at a time": [BODY[index : index + 1] for index in range(len(BODY))],
}


@pytest.mark.parametrize("chunking", sorted(CHUNKINGS))
def test_one_body_digests_the_same_however_the_read_was_chunked(durable_state: ModuleType, chunking: str) -> None:
    """The chunking is the transfer's, so it must not reach the snapshot.

    Two reads of one unchanged object can be split differently, and a digest
    that framed those lengths would move -- so every row comparing a snapshot
    would fail for a deployment that had changed nothing. A body is one value,
    so unlike a record it has no boundary between values to lose by hashing it
    raw.
    """
    whole = durable_state.object_line("runs/r-1/manifest.json", [BODY])

    assert durable_state.object_line("runs/r-1/manifest.json", CHUNKINGS[chunking]) == whole


def test_a_records_digest_still_frames_the_values_it_covers(durable_state: ModuleType) -> None:
    """The two digests answer different questions, so relaxing one must not relax the other.

    A record is a sequence of values and a body is one value. Framing is what
    keeps a value from moving between columns unseen, and the object digest is
    not entitled to relax it on a record's behalf.

    The case is a value and a null trading places, because that is the one
    framing catches and the tag does not: a null renders to nothing, so both
    records concatenate to the same bytes.
    """
    holding = durable_state.table_lines("product_runs", [("a", None)])
    swapped = durable_state.table_lines("product_runs", [(None, "a")])

    assert holding != swapped


def test_the_same_durable_state_read_twice_is_the_same_snapshot(durable_state: ModuleType) -> None:
    """A comparison that nothing can pass would fail every row that makes it."""
    assert snapshot(durable_state, ROWS, OBJECTS) == snapshot(durable_state, ROWS, OBJECTS)


def test_the_order_a_server_returned_the_rows_in_is_not_a_change(durable_state: ModuleType) -> None:
    """No table here is read with an ORDER BY it is guaranteed a unique key for.

    So the snapshot orders what it digests itself, and two reads of one
    unchanged table compare equal however the server listed them.
    """
    reversed_rows = {table: list(reversed(rows)) for table, rows in ROWS.items()}

    assert snapshot(durable_state, reversed_rows, OBJECTS) == snapshot(durable_state, ROWS, OBJECTS)


def test_the_count_line_stays_the_bare_count_the_driver_reads(durable_state: ModuleType) -> None:
    """`snapshot_count` reads `^table <name> ` and refuses anything but digits.

    A count with a digest appended is not a count, and the three rows that prove
    there is state to preserve would refuse every deployment.
    """
    lines = durable_state.table_lines("product_runs", ROWS["product_runs"])

    assert lines[0] == r"table product_runs 2", f"the driver cannot read {lines[0]!r} as a count"
    assert len(lines) == 2, "a table contributes something other than its count and its contents"


def test_no_value_either_store_holds_reaches_the_snapshot(durable_state: ModuleType) -> None:
    """A digest is not the value it covers, and these records hold declared configuration.

    What may print is the deployment's own table and object names, the counts,
    and the digests. The rows hold a registered package's declared content and
    the objects hold what a run planned against a destination; neither belongs
    in a line a driver shows.
    """
    printed = snapshot(durable_state, ROWS, OBJECTS)

    for held in ("checksum-abcd", "qualification", "operations", "create", "beef", "succeeded", "plan\n"):
        assert held not in printed, f"{held!r} was read out of a store and printed"


# ---------------------------------------------------------------------------
# The snapshot's own reads, driven through clients shaped like the real ones
# ---------------------------------------------------------------------------
# `main` reaches two stores this suite has neither of, so the clients are
# supplied instead. They are not stand-ins for the stores' behaviour -- the
# renderings above already cover that -- but for their *shapes*, because one of
# those shapes is where a body stopped being streamable.
class RawStream:
    """What botocore hands back from a body's context manager.

    `StreamingBody.__enter__` returns `self._raw_stream`, a urllib3
    `HTTPResponse`, and that object carries no `iter_chunks`. A fake whose
    `__enter__` returned the wrapper would be the one shape this defect cannot
    appear in, so this one does what botocore does.
    """

    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FakeBody:
    """One object's body, with botocore's own wrapper and context-manager behaviour."""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks
        self.raw = RawStream()
        self.closed = False

    def __enter__(self) -> RawStream:
        return self.raw

    def __exit__(self, *exception: object) -> None:
        self.raw.close()

    def iter_chunks(self, chunk_size: int) -> Iterator[bytes]:
        assert chunk_size > 0, "a body was streamed without a chunk size"
        yield from self._chunks

    def close(self) -> None:
        self.closed = True
        self.raw.close()


class FakePaginator:
    def __init__(self, keys: list[str]) -> None:
        self._keys = keys

    def paginate(self, **query: str) -> list[dict[str, list[dict[str, str]]]]:
        assert "Bucket" in query, "the listing named no bucket"
        return [{"Contents": [{"Key": key} for key in self._keys]}]


class FakeStore:
    def __init__(self, bodies: dict[str, FakeBody]) -> None:
        self._bodies = bodies
        self.requested: list[str] = []

    def get_paginator(self, name: str) -> FakePaginator:
        assert name == "list_objects_v2", f"the snapshot paginated {name}"
        return FakePaginator(sorted(self._bodies))

    def get_object(self, **query: str) -> dict[str, FakeBody]:
        self.requested.append(query["Key"])
        return {"Body": self._bodies[query["Key"]]}


class FakeSQL:
    """Just enough of `psycopg.sql` to compose the one statement the snapshot builds."""

    def __init__(self, text: str) -> None:
        self.text = text

    def format(self, *parts: FakeSQL) -> FakeSQL:
        return FakeSQL(self.text.format(*(part.text for part in parts)))

    def join(self, parts: Iterable[FakeSQL]) -> FakeSQL:
        return FakeSQL(self.text.join(part.text for part in parts))


# The one table the driven snapshot holds, the one record in it, and the one
# object beside it. The record is held by column name so the cursor can return
# the projection the statement actually asked for.
FAKE_TABLE = "product_runs"
FAKE_RECORD = {"phase": "succeeded", "run_id": "r-1"}
FAKE_KEY = "runs/r-1/manifest.json"


class FakeCursor:
    """Answers the three reads the snapshot makes, told apart by the statement."""

    def __init__(self) -> None:
        self._rows: list[tuple[object, ...]] = []
        self.composed: str | None = None

    def execute(self, statement: object, params: tuple[object, ...] | None = None) -> None:
        if isinstance(statement, str) and "information_schema.tables" in statement:
            self._rows = [(FAKE_TABLE,)]
        elif isinstance(statement, str) and "information_schema.columns" in statement:
            assert params == (FAKE_TABLE,), f"the columns were read for {params}"
            self._rows = [(column,) for column in sorted(FAKE_RECORD)]
        else:
            assert isinstance(statement, FakeSQL), f"the record read is not a composed statement: {statement!r}"
            self.composed = statement.text
            # The projection the statement asked for, so an excluded column is
            # absent from the row as well as from the read.
            selected = re.findall(r'"(\w+)"', statement.text)
            assert selected[-1] == FAKE_TABLE, f"the statement does not name {FAKE_TABLE} last"
            self._rows = [tuple(FAKE_RECORD[name] for name in selected[:-1])]

    def fetchall(self) -> list[tuple[object, ...]]:
        return self._rows

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exception: object) -> None:
        return None


class FakeConnection:
    def __init__(self, cursor: FakeCursor) -> None:
        self._cursor = cursor

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exception: object) -> None:
        return None

    def cursor(self) -> FakeCursor:
        return self._cursor


def install_stores(monkeypatch: pytest.MonkeyPatch, body: FakeBody) -> tuple[FakeStore, FakeCursor]:
    """Give `main` its settings and both clients, as importable modules.

    Injected through `sys.modules` because `main` imports the drivers itself.
    That is also what lets this run on an install where neither is present.
    """
    for name, value in (
        ("INFRAHUB_SYNC_DATABASE_URL", "postgresql://product@postgres:5432/infrahub_sync"),
        ("INFRAHUB_SYNC_S3_ENDPOINT_URL", "http://object-store:9000"),
        ("INFRAHUB_SYNC_S3_BUCKET", "infrahub-sync"),
    ):
        monkeypatch.setenv(name, value)

    store = FakeStore({FAKE_KEY: body})
    cursor = FakeCursor()
    monkeypatch.setitem(sys.modules, "boto3", SimpleNamespace(client=lambda *_, **__: store))
    monkeypatch.setitem(
        sys.modules,
        "psycopg",
        SimpleNamespace(
            connect=lambda *_, **__: FakeConnection(cursor),
            sql=SimpleNamespace(SQL=FakeSQL, Identifier=lambda name: FakeSQL(f'"{name}"')),
        ),
    )
    return store, cursor


def test_the_snapshot_streams_a_body_through_the_wrapper_that_can_stream_it(
    durable_state: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A body's context manager hands back the raw stream, which cannot stream chunks.

    So the wrapper is what has to be kept and closed. Entering it rebinds the
    name to a urllib3 `HTTPResponse`, and the object half of the snapshot then
    ends the row it was taken for.
    """
    chunks = [b'{"checksum": ', b'"abcd"}']
    body = FakeBody(chunks)
    store, _ = install_stores(monkeypatch, body)

    durable_state.main()

    printed = capsys.readouterr().out.splitlines()
    assert printed[0] == f"table {FAKE_TABLE} 1"
    assert printed[1].startswith(f"contents {FAKE_TABLE} ")
    assert printed[2] == f"object {FAKE_KEY} {sha256(b''.join(chunks)).hexdigest()}"
    assert store.requested == [FAKE_KEY], "the snapshot did not read the one object it listed"
    assert body.closed, "the body wrapper was left open"


def test_an_excluded_column_reaches_neither_the_read_nor_the_record(
    durable_state: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The composed statement is the one thing the renderings above never see.

    They are handed rows some statement already returned, so whether an
    exclusion is applied at the boundary that reads them is only observable by
    driving the read. Both statements are asserted, so an exclusion that
    narrowed nothing and one that narrowed everything are told apart.
    """
    body = FakeBody([b"{}"])
    _, cursor = install_stores(monkeypatch, body)

    monkeypatch.setattr(durable_state, "VOLATILE_COLUMNS", {})
    durable_state.main()
    whole = cursor.composed

    monkeypatch.setattr(durable_state, "VOLATILE_COLUMNS", {FAKE_TABLE: ("phase",)})
    durable_state.main()
    narrowed = cursor.composed
    capsys.readouterr()

    assert whole == 'SELECT "phase", "run_id" FROM "product_runs"'
    assert narrowed == 'SELECT "run_id" FROM "product_runs"'


def test_an_exclusion_that_leaves_a_table_with_nothing_to_compare_ends_the_check(
    durable_state: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A table with no column left would report equal for every deployment there is."""
    _, _ = install_stores(monkeypatch, FakeBody([b"{}"]))
    monkeypatch.setattr(durable_state, "VOLATILE_COLUMNS", {FAKE_TABLE: tuple(FAKE_RECORD)})

    with pytest.raises(SystemExit) as refused:
        durable_state.main()

    assert f"leave {FAKE_TABLE} with no column to compare" in str(refused.value)
