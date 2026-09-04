"""The deterministic run-bundle container and its untrusted-archive boundary."""

from __future__ import annotations

import json
import os
import stat
import subprocess  # noqa: S404 - fixed local interpreter probes cross-process determinism.
import sys
import zipfile
from collections.abc import Callable
from hashlib import sha256
from io import BytesIO
from pathlib import Path

import pytest

from infrahub_sync.product_store.bundle import (
    BUNDLE_MANIFEST_NAME,
    MAX_BUNDLE_BYTES,
    MAX_BUNDLE_MEMBERS,
    BundleFormatError,
    extract_bundle,
    read_bundle,
    write_bundle,
)

# The archive these members produce. Pinned as a literal so the determinism cases fail
# on any host where the writer stops being reproducible, not only on this one.
_ARCHIVE_DIGEST = "fbe4fca5318378df86862dee86d8b2885dbf889f1a312d4dd5086d67fd14750b"
_ARCHIVE_SIZE = 1254

_MEMBERS = {
    "plan/operations.jsonl": b'{"op": "create", "id": "1"}\n{"op": "update", "id": "2"}\n',
    "plan/manifest.json": b'{"checksum": "' + b"a" * 64 + b'"}',
    "A/devices.parquet": b"PAR1\x00\x01\x02\x03PAR1",
    "A/interfaces.parquet": b"PAR1\xff\xfe\xfd\xfcPAR1",
}


def _entries(data: bytes) -> list[zipfile.ZipInfo]:
    with zipfile.ZipFile(BytesIO(data)) as archive:
        return list(archive.infolist())


def _manifest_for(
    members: dict[str, bytes],
    *,
    format_version: object = 1,
    sizes: dict[str, object] | None = None,
) -> bytes:
    """A manifest that agrees with `members`, so a case isolates the field it changes."""
    declared = sizes or {}
    return json.dumps(
        {
            "format_version": format_version,
            "members": [
                {
                    "path": path,
                    "size": declared.get(path, len(members[path])),
                    "sha256": sha256(members[path]).hexdigest(),
                }
                for path in sorted(members)
            ],
        }
    ).encode()


def _rebuild(members: dict[str, bytes], *, manifest: bytes | None = None) -> bytes:
    """Assemble an archive directly, so a corpus case can violate the writer grammar."""
    valid = write_bundle(_MEMBERS)
    with zipfile.ZipFile(BytesIO(valid)) as archive:
        declared = archive.read(BUNDLE_MANIFEST_NAME)
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr(BUNDLE_MANIFEST_NAME, declared if manifest is None else manifest)
        for name, payload in members.items():
            archive.writestr(name, payload)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Byte-for-byte determinism
# ---------------------------------------------------------------------------


def test_the_members_serialize_to_the_pinned_archive_vector() -> None:
    """A literal vector, because "equal to itself" passes for a clock-dependent writer.

    Any drift in timestamp, permissions, host system, member order, compression method,
    or manifest encoding moves this digest, on this host or any other.
    """
    archive = write_bundle(_MEMBERS)

    assert len(archive) == _ARCHIVE_SIZE
    assert sha256(archive).hexdigest() == _ARCHIVE_DIGEST


def test_member_insertion_order_does_not_reach_the_bytes() -> None:
    """The writer orders members itself, so an unordered caller mapping cannot vary output."""
    reversed_members = dict(reversed(list(_MEMBERS.items())))

    assert reversed_members != _MEMBERS or list(reversed_members) != list(_MEMBERS)
    assert write_bundle(reversed_members) == write_bundle(_MEMBERS)


def test_no_member_is_compressed() -> None:
    """zlib output is not stable across builds, so a compressed member breaks determinism."""
    assert [entry.compress_type for entry in _entries(write_bundle(_MEMBERS))] == [zipfile.ZIP_STORED] * (
        len(_MEMBERS) + 1
    )


def test_timestamps_permissions_and_host_system_are_pinned() -> None:
    """The three ZIP header fields that otherwise vary by clock, umask, and platform."""
    entries = _entries(write_bundle(_MEMBERS))

    assert {entry.date_time for entry in entries} == {(1980, 1, 1, 0, 0, 0)}
    assert {entry.external_attr for entry in entries} == {(stat.S_IFREG | 0o644) << 16}
    assert {entry.create_system for entry in entries} == {0}


def test_bytes_are_identical_across_processes() -> None:
    """The same vector, reproduced by interpreters that share no state with this one."""
    script = (
        "import sys, json;"
        "from hashlib import sha256;"
        "from infrahub_sync.product_store.bundle import write_bundle;"
        "members={k.encode().decode(): bytes.fromhex(v) for k, v in json.loads(sys.argv[1]).items()};"
        "sys.stdout.write(sha256(write_bundle(members)).hexdigest())"
    )
    payload = __import__("json").dumps({name: data.hex() for name, data in _MEMBERS.items()})
    digests = set()
    for seed in ("0", "1", "12345"):
        environment = {**os.environ, "PYTHONHASHSEED": seed}
        result = subprocess.run(  # noqa: S603 - fixed local interpreter, no shell.
            [sys.executable, "-c", script, payload],
            capture_output=True,
            check=True,
            env=environment,
            text=True,
        )
        digests.add(result.stdout.strip())

    assert digests == {_ARCHIVE_DIGEST}


def test_a_bundle_round_trips_through_its_own_reader() -> None:
    assert read_bundle(write_bundle(_MEMBERS)) == _MEMBERS


# ---------------------------------------------------------------------------
# Writer grammar
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        pytest.param("/plan/operations.jsonl", id="absolute"),
        pytest.param("../plan/operations.jsonl", id="parent-traversal"),
        pytest.param("plan/../../operations.jsonl", id="embedded-traversal"),
        pytest.param("plan/./operations.jsonl", id="dot-segment"),
        pytest.param("operations.jsonl", id="single-segment"),
        pytest.param("plan/nested/operations.jsonl", id="three-segments"),
        pytest.param("plan//operations.jsonl", id="empty-segment"),
        pytest.param("plan\\operations.jsonl", id="backslash-separator"),
        pytest.param("C:/plan/operations.jsonl", id="drive-letter"),
        pytest.param("plan/", id="trailing-separator"),
        pytest.param("plan/.hidden", id="leading-dot"),
        pytest.param("plan/oper ations.jsonl", id="space"),
        pytest.param("plan/opérations.jsonl", id="non-ascii"),
        pytest.param("plan/operations\x00.jsonl", id="null-byte"),
        pytest.param(BUNDLE_MANIFEST_NAME, id="reserved-manifest-name"),
        pytest.param("", id="empty"),
    ],
)
def test_the_writer_refuses_a_member_path_outside_its_grammar(path: str) -> None:
    """The writer states its accepted domain rather than filtering known-bad examples."""
    with pytest.raises(BundleFormatError):
        write_bundle({path: b"payload"})


def test_the_writer_refuses_a_bundle_over_the_size_bound() -> None:
    with pytest.raises(BundleFormatError):
        write_bundle({"A/huge.parquet": b"\x00" * (MAX_BUNDLE_BYTES + 1)})


def test_the_writer_refuses_an_empty_bundle() -> None:
    with pytest.raises(BundleFormatError):
        write_bundle({})


def test_a_member_path_ending_in_a_newline_is_refused() -> None:
    """An anchored `$` also matches before a final newline; the grammar is matched in full."""
    with pytest.raises(BundleFormatError) as writer_refusal:
        write_bundle({"A/devices.parquet\n": b"payload"})

    assert writer_refusal.value.reason == "bundle-member-path-rejected"

    with pytest.raises(BundleFormatError) as reader_refusal:
        read_bundle(_rebuild({"A/devices.parquet\n": b"payload"}))

    assert reader_refusal.value.reason == "bundle-member-path-rejected"


def test_the_writer_refuses_case_colliding_member_names() -> None:
    """Its own reader refuses these, so emitting them would produce an unreadable bundle."""
    with pytest.raises(BundleFormatError) as refusal:
        write_bundle({"A/devices.parquet": b"one", "a/devices.parquet": b"two"})

    assert refusal.value.reason == "bundle-case-collision"


def test_the_writer_refuses_more_members_than_a_bundle_holds() -> None:
    members = {f"A/m{index:05d}.parquet": b"" for index in range(MAX_BUNDLE_MEMBERS + 1)}

    with pytest.raises(BundleFormatError) as refusal:
        write_bundle(members)

    assert refusal.value.reason == "bundle-too-many-members"


def test_the_writer_refuses_an_archive_that_only_its_framing_pushes_over_the_bound() -> None:
    """Payload alone is not the archive: local headers and the directory count too."""
    with pytest.raises(BundleFormatError) as refusal:
        write_bundle({"A/huge.parquet": b"\x00" * (MAX_BUNDLE_BYTES - 200)})

    assert refusal.value.reason == "bundle-too-large"


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(lambda: dict(_MEMBERS), id="ordinary"),
        pytest.param(
            lambda: {f"A/m{index:05d}.parquet": b"x" for index in range(MAX_BUNDLE_MEMBERS)},
            id="most-members",
        ),
        pytest.param(lambda: {"A/huge.parquet": b"\x00" * (MAX_BUNDLE_BYTES - 1024)}, id="largest-payload"),
    ],
)
def test_the_reader_accepts_every_archive_the_writer_returns(build: Callable[[], dict[str, bytes]]) -> None:
    """At each bound's edge the writer's accepted domain stays inside the reader's."""
    members = build()

    data = write_bundle(members)

    assert len(data) <= MAX_BUNDLE_BYTES
    assert read_bundle(data) == members


# ---------------------------------------------------------------------------
# Hostile-archive property classes
# ---------------------------------------------------------------------------


def test_a_duplicate_member_name_is_refused() -> None:
    """Two entries under one name make "which bytes" a reader-implementation detail."""
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr(BUNDLE_MANIFEST_NAME, b"{}")
        archive.writestr("plan/manifest.json", b"first")
        archive.writestr("plan/manifest.json", b"second")

    with pytest.raises(BundleFormatError) as refusal:
        read_bundle(buffer.getvalue())

    assert refusal.value.reason == "bundle-duplicate-member"


def test_case_colliding_member_names_are_refused() -> None:
    """Two names that differ only by case become one file on a case-folding filesystem."""
    with pytest.raises(BundleFormatError):
        read_bundle(_rebuild({"plan/manifest.json": b"a", "plan/MANIFEST.json": b"b"}))


@pytest.mark.parametrize(
    "path",
    [
        pytest.param("/etc/passwd", id="absolute"),
        pytest.param("../../etc/passwd", id="parent-traversal"),
        pytest.param("plan/../../../etc/passwd", id="embedded-traversal"),
        pytest.param("plan\\..\\..\\etc\\passwd", id="backslash-traversal"),
    ],
)
def test_a_member_path_escaping_the_destination_is_refused(path: str) -> None:
    with pytest.raises(BundleFormatError):
        read_bundle(_rebuild({path: b"payload"}))


def test_a_symlink_member_is_refused() -> None:
    """A symlink entry turns extraction into a write through a path the archive chose.

    The name satisfies the grammar, so only the entry's file type can refuse it.
    """
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr(BUNDLE_MANIFEST_NAME, b"{}")
        entry = zipfile.ZipInfo("plan/manifest.json")
        entry.external_attr = (stat.S_IFLNK | 0o777) << 16
        entry.create_system = 3
        archive.writestr(entry, b"/etc/passwd")

    with pytest.raises(BundleFormatError) as refusal:
        read_bundle(buffer.getvalue())

    assert refusal.value.reason == "bundle-non-regular-member"


@pytest.mark.parametrize(
    "mode",
    [
        pytest.param(stat.S_IFDIR, id="directory"),
        pytest.param(stat.S_IFCHR, id="character-device"),
        pytest.param(stat.S_IFBLK, id="block-device"),
        pytest.param(stat.S_IFIFO, id="fifo"),
        pytest.param(stat.S_IFSOCK, id="socket"),
    ],
)
def test_a_member_that_is_not_a_regular_file_is_refused(mode: int) -> None:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr(BUNDLE_MANIFEST_NAME, b"{}")
        entry = zipfile.ZipInfo("plan/manifest.json")
        entry.external_attr = (mode | 0o644) << 16
        entry.create_system = 3
        archive.writestr(entry, b"payload")

    with pytest.raises(BundleFormatError):
        read_bundle(buffer.getvalue())


@pytest.mark.parametrize(
    "compression",
    [
        pytest.param(zipfile.ZIP_DEFLATED, id="deflate"),
        pytest.param(zipfile.ZIP_BZIP2, id="bzip2"),
        pytest.param(zipfile.ZIP_LZMA, id="lzma"),
    ],
)
def test_a_compressed_member_is_refused(compression: int) -> None:
    """Refusing the method is what keeps a decompression bomb out of the size bound."""
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(BUNDLE_MANIFEST_NAME, b"{}")
        archive.writestr(zipfile.ZipInfo("plan/manifest.json"), b"payload", compress_type=compression)

    with pytest.raises(BundleFormatError):
        read_bundle(buffer.getvalue())


def test_an_undeclared_member_is_refused() -> None:
    """Every entry has to be accounted for, not merely every declared entry present."""
    with pytest.raises(BundleFormatError):
        read_bundle(_rebuild({**_MEMBERS, "A/smuggled.parquet": b"extra"}))


def test_a_missing_declared_member_is_refused() -> None:
    remaining = {name: data for name, data in _MEMBERS.items() if name != "A/devices.parquet"}

    with pytest.raises(BundleFormatError):
        read_bundle(_rebuild(remaining))


def test_a_member_whose_bytes_do_not_match_the_manifest_is_refused() -> None:
    tampered = {**_MEMBERS, "plan/operations.jsonl": _MEMBERS["plan/operations.jsonl"] + b"\n"}

    with pytest.raises(BundleFormatError):
        read_bundle(_rebuild(tampered))


@pytest.mark.parametrize(
    "manifest",
    [
        pytest.param(b"", id="empty"),
        pytest.param(b"not json", id="unparsable"),
        pytest.param(b"[]", id="not-an-object"),
        pytest.param(b'{"format_version": 2, "members": []}', id="unsupported-version"),
        pytest.param(b'{"members": []}', id="version-absent"),
        pytest.param(b'{"format_version": 1}', id="members-absent"),
    ],
)
def test_an_unusable_manifest_is_refused(manifest: bytes) -> None:
    with pytest.raises(BundleFormatError):
        read_bundle(_rebuild(_MEMBERS, manifest=manifest))


def test_an_archive_without_a_manifest_is_refused() -> None:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("plan/manifest.json", b"payload")

    with pytest.raises(BundleFormatError):
        read_bundle(buffer.getvalue())


def test_bytes_that_are_not_a_zip_archive_are_refused() -> None:
    with pytest.raises(BundleFormatError):
        read_bundle(b"PK\x03\x04 truncated and invalid")


def test_an_archive_over_the_size_bound_is_refused_before_parsing() -> None:
    """These bytes are not a readable archive, so the reason names which check ran first."""
    with pytest.raises(BundleFormatError) as refusal:
        read_bundle(b"\x00" * (MAX_BUNDLE_BYTES + 1))

    assert refusal.value.reason == "bundle-too-large"


def test_a_manifest_whose_version_is_a_boolean_is_refused() -> None:
    """JSON `true` equals 1, so a version has to be a plain integer, not merely equal to one."""
    manifest = _manifest_for(_MEMBERS, format_version=True)

    with pytest.raises(BundleFormatError) as refusal:
        read_bundle(_rebuild(_MEMBERS, manifest=manifest))

    assert refusal.value.reason == "bundle-manifest-invalid"


def test_a_manifest_whose_member_size_is_a_boolean_is_refused() -> None:
    """`true` also passes a length comparison against a one-byte member."""
    members = {"A/devices.parquet": b"x"}
    manifest = _manifest_for(members, sizes={"A/devices.parquet": True})

    with pytest.raises(BundleFormatError) as refusal:
        read_bundle(_rebuild(members, manifest=manifest))

    assert refusal.value.reason == "bundle-manifest-invalid"


def test_a_manifest_declaring_a_negative_member_size_is_refused() -> None:
    """A length is refused where the manifest is parsed, not incidentally at verification."""
    members = {"A/devices.parquet": b"x"}
    manifest = _manifest_for(members, sizes={"A/devices.parquet": -1})

    with pytest.raises(BundleFormatError) as refusal:
        read_bundle(_rebuild(members, manifest=manifest))

    assert refusal.value.reason == "bundle-manifest-invalid"


def test_an_archive_holding_more_entries_than_a_bundle_holds_is_refused() -> None:
    """A small archive can still declare enough entries to make reading it disproportionate."""
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr(BUNDLE_MANIFEST_NAME, b"{}")
        for index in range(MAX_BUNDLE_MEMBERS + 1):
            archive.writestr(f"A/m{index:05d}.parquet", b"")
    data = buffer.getvalue()

    with pytest.raises(BundleFormatError) as refusal:
        read_bundle(data)

    assert refusal.value.reason == "bundle-too-many-members"
    assert len(data) < MAX_BUNDLE_BYTES


def test_a_manifest_declaring_more_members_than_a_bundle_holds_is_refused() -> None:
    """The manifest's own table is bounded before it is built, not after."""
    overdeclared = json.dumps(
        {
            "format_version": 1,
            "members": [
                {"path": f"A/m{index:05d}.parquet", "size": 0, "sha256": "a" * 64}
                for index in range(MAX_BUNDLE_MEMBERS + 1)
            ],
        }
    ).encode()

    with pytest.raises(BundleFormatError) as refusal:
        read_bundle(_rebuild({"A/m00000.parquet": b""}, manifest=overdeclared))

    assert refusal.value.reason == "bundle-too-many-members"


def test_a_decompression_bomb_is_refused_on_its_method_before_its_declared_size() -> None:
    """Storing members uncompressed is what makes the archive bound bound the members."""
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(BUNDLE_MANIFEST_NAME, b"{}")
        archive.writestr("A/bomb.parquet", b"\x00" * (MAX_BUNDLE_BYTES + 1))

    with pytest.raises(BundleFormatError) as refusal:
        read_bundle(buffer.getvalue())

    assert refusal.value.reason == "bundle-unsupported-compression"


# ---------------------------------------------------------------------------
# All-or-nothing extraction
# ---------------------------------------------------------------------------


def test_extraction_writes_every_member_into_a_new_directory(tmp_path: Path) -> None:
    destination = tmp_path / "stage-private"

    extract_bundle(write_bundle(_MEMBERS), destination)

    extracted = {
        str(path.relative_to(destination).as_posix()): path.read_bytes()
        for path in destination.rglob("*")
        if path.is_file()
    }
    assert extracted == _MEMBERS


def test_extraction_refuses_a_destination_that_already_exists(tmp_path: Path) -> None:
    """A stage-private directory is new by definition; reusing one blends two runs."""
    destination = tmp_path / "stage-private"
    destination.mkdir()

    with pytest.raises(BundleFormatError):
        extract_bundle(write_bundle(_MEMBERS), destination)


def test_a_refused_archive_leaves_no_destination_behind(tmp_path: Path) -> None:
    """All-or-nothing: a partially valid archive must not leave its valid members."""
    destination = tmp_path / "stage-private"
    corpus = _rebuild({**_MEMBERS, "A/smuggled.parquet": b"extra"})

    with pytest.raises(BundleFormatError):
        extract_bundle(corpus, destination)

    assert not destination.exists()
    assert list(tmp_path.iterdir()) == []
