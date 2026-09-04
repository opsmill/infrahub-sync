"""The internal run-bundle container: one deterministic archive per stage checkpoint.

A bundle carries the files one stage needs to hand to the next. Two properties make it
usable as durable handoff state, and both are enforced here rather than by convention:

Identical members always produce identical bytes, on any host and in any process. That
is what lets a fixed checkpoint identifier be retried safely — the retry either presents
the same digest and is the same publication, or it presents a different one and is a
conflict. Members are therefore stored uncompressed: zlib output is not guaranteed
identical across zlib builds or Python versions, so a compressed bundle could verify on
the host that wrote it and fail on the host that reads it.

An archive read back from storage is untrusted input. Every structural property is
checked against the whole archive before a single byte is written to disk, and the
destination directory appears only once all members are known to be good.
"""

from __future__ import annotations

import json
import stat
import zipfile
from contextlib import suppress
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from re import compile as compile_pattern
from shutil import rmtree
from tempfile import mkdtemp
from typing import TYPE_CHECKING, Any

from infrahub_sync.plan.canonical import canonical_json_bytes

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping

__all__ = [
    "BUNDLE_FORMAT_VERSION",
    "BUNDLE_MANIFEST_NAME",
    "BUNDLE_MEDIA_TYPE",
    "FINAL_CHECKPOINT_ARTIFACT_ID",
    "MAX_BUNDLE_BYTES",
    "MAX_BUNDLE_MEMBERS",
    "PLAN_CHECKPOINT_ARTIFACT_ID",
    "BundleFormatError",
    "extract_bundle",
    "read_bundle",
    "write_bundle",
]

BUNDLE_FORMAT_VERSION = 1
BUNDLE_MANIFEST_NAME = "bundle-manifest.json"
BUNDLE_MEDIA_TYPE = "application/vnd.infrahub-sync.run-bundle.v1+zip"
MAX_BUNDLE_BYTES = 64 * 1024 * 1024
# The most members one bundle may hold, not counting the manifest. A checkpoint carries
# the plan's two files plus one Parquet file per extracted resource kind, so this sits far
# above any real configuration. It exists because the byte bound alone does not bound the
# entry count: an archive of empty members stays small while costing the reader one entry,
# one set element, and one dictionary entry each.
MAX_BUNDLE_MEMBERS = 1024

# Fixed for the lifetime of format version 1: a stage looks up its predecessor's bundle
# by this identifier, so the two are part of the protocol rather than naming choices.
PLAN_CHECKPOINT_ARTIFACT_ID = "run-bundle-v1-plan"
FINAL_CHECKPOINT_ARTIFACT_ID = "run-bundle-v1-final"

# The ZIP epoch, chosen because it is the earliest a ZIP timestamp can express. Any
# real clock reading would make the bytes depend on when they were produced.
_FIXED_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
_REGULAR_FILE_ATTRIBUTES = (stat.S_IFREG | 0o644) << 16
# MS-DOS. ZipInfo otherwise records the writing platform, which would make a bundle
# built on Linux differ from the same bundle built on macOS.
_MSDOS_CREATE_SYSTEM = 0
_ENCRYPTED_FLAG = 0x1

# Two path segments, each starting alphanumeric and continuing in an explicitly listed
# ASCII set. Stating the accepted domain this way is what excludes absolute paths, `..`
# traversal, empty and dot segments, backslashes, drive letters, control characters, and
# non-ASCII homoglyphs, without enumerating any of them.
_MEMBER_PATH = compile_pattern(r"[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*")

# The refusal vocabulary. A caller that has to react differently per property reads
# `BundleFormatError.reason` rather than a message, and the store maps these onto its own
# artifact lookup reasons.
EMPTY = "bundle-empty"
TOO_LARGE = "bundle-too-large"
MEMBER_PATH_REJECTED = "bundle-member-path-rejected"
DUPLICATE_MEMBER = "bundle-duplicate-member"
CASE_COLLISION = "bundle-case-collision"
TOO_MANY_MEMBERS = "bundle-too-many-members"
MANIFEST_ABSENT = "bundle-manifest-absent"
MANIFEST_INVALID = "bundle-manifest-invalid"
UNSUPPORTED_COMPRESSION = "bundle-unsupported-compression"
ENCRYPTED_MEMBER = "bundle-encrypted-member"
NON_REGULAR_MEMBER = "bundle-non-regular-member"
MEMBER_TOO_LARGE = "bundle-member-too-large"
MEMBER_MISMATCH = "bundle-member-mismatch"
MEMBER_INTEGRITY_FAILED = "bundle-member-integrity-failed"
UNREADABLE = "bundle-unreadable"
DESTINATION_EXISTS = "bundle-destination-exists"


class BundleFormatError(ValueError):
    """A bundle does not satisfy the format's accepted domain.

    Carries a stable `reason` so a caller can distinguish which property failed without
    parsing a message.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def write_bundle(members: Mapping[str, bytes]) -> bytes:
    """Serialize `members` into the deterministic uncompressed bundle archive.

    Every archive this returns is one `read_bundle` accepts: the writer holds itself to
    the reader's accepted domain rather than to a looser one. Raises `BundleFormatError`
    when the members are outside that domain or the completed archive exceeds the size
    bound.
    """
    if not members:
        raise BundleFormatError(EMPTY)
    if len(members) > MAX_BUNDLE_MEMBERS:
        raise BundleFormatError(TOO_MANY_MEMBERS)
    for path in members:
        _require_member_path(path)
    _require_distinct_when_case_folded(members.keys())
    manifest = _manifest_bytes(members)
    if len(manifest) + sum(len(payload) for payload in members.values()) > MAX_BUNDLE_BYTES:
        raise BundleFormatError(TOO_LARGE)
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        _write_entry(archive, BUNDLE_MANIFEST_NAME, manifest)
        for path in sorted(members):
            _write_entry(archive, path, members[path])
    archive_bytes = buffer.getvalue()
    # The payload check above cannot see ZIP local headers, the central directory, or the
    # end-of-archive record, and the bound the reader applies covers all of them. Only the
    # completed archive answers the question the reader will ask.
    if len(archive_bytes) > MAX_BUNDLE_BYTES:
        raise BundleFormatError(TOO_LARGE)
    return archive_bytes


def read_bundle(data: bytes) -> dict[str, bytes]:
    """Validate the complete archive and return its members.

    Nothing about the archive's structure is trusted before it is checked, and no member
    is returned until every member has been verified against the manifest.
    """
    if len(data) > MAX_BUNDLE_BYTES:
        raise BundleFormatError(TOO_LARGE)
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            entries = archive.infolist()
            _require_sound_entries(entries)
            declared = _declared_members(archive.read(BUNDLE_MANIFEST_NAME))
            present = {entry.filename for entry in entries if entry.filename != BUNDLE_MANIFEST_NAME}
            if declared.keys() != present:
                raise BundleFormatError(MEMBER_MISMATCH)
            return {name: _verified(archive.read(name), declared[name]) for name in sorted(present)}
    except zipfile.BadZipFile:
        raise BundleFormatError(UNREADABLE) from None


def extract_bundle(data: bytes, destination: Path) -> None:
    """Validate `data` completely, then place every member under a new `destination`.

    The destination is created by renaming a fully populated private directory, so it
    either holds the whole bundle or does not exist.
    """
    if destination.exists():
        raise BundleFormatError(DESTINATION_EXISTS)
    members = read_bundle(data)
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(mkdtemp(prefix=f".{destination.name}-", dir=destination.parent))
    try:
        for path, payload in members.items():
            target = staging.joinpath(*path.split("/"))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
        staging.replace(destination)
    except BaseException:
        with suppress(OSError):
            rmtree(staging)
        raise


def _require_member_path(path: str) -> None:
    # `fullmatch`, never `match`: an anchored `$` also matches immediately before a final
    # newline, which would put a control character inside the grammar's accepted domain.
    if not _MEMBER_PATH.fullmatch(path):
        raise BundleFormatError(MEMBER_PATH_REJECTED)


def _require_distinct_when_case_folded(names: Collection[str]) -> None:
    """Two names differing only by case become one file on a case-folding filesystem."""
    if len({name.lower() for name in names}) != len(names):
        raise BundleFormatError(CASE_COLLISION)


def _write_entry(archive: zipfile.ZipFile, path: str, payload: bytes) -> None:
    entry = zipfile.ZipInfo(path, date_time=_FIXED_TIMESTAMP)
    entry.compress_type = zipfile.ZIP_STORED
    entry.external_attr = _REGULAR_FILE_ATTRIBUTES
    entry.create_system = _MSDOS_CREATE_SYSTEM
    archive.writestr(entry, payload)


def _manifest_bytes(members: Mapping[str, bytes]) -> bytes:
    return canonical_json_bytes(
        {
            "format_version": BUNDLE_FORMAT_VERSION,
            "members": [
                {"path": path, "size": len(members[path]), "sha256": sha256(members[path]).hexdigest()}
                for path in sorted(members)
            ],
        }
    )


def _require_sound_entries(entries: list[zipfile.ZipInfo]) -> None:
    """Reject every structural property that makes an archive unsafe to extract.

    The entry count is bounded first. Every later check builds a list, a set, or a
    dictionary over the entries, so the bound has to apply before any of them exists.
    One entry above the member limit is the manifest.
    """
    if len(entries) > MAX_BUNDLE_MEMBERS + 1:
        raise BundleFormatError(TOO_MANY_MEMBERS)
    names = [entry.filename for entry in entries]
    if len(set(names)) != len(names):
        raise BundleFormatError(DUPLICATE_MEMBER)
    _require_distinct_when_case_folded(names)
    if BUNDLE_MANIFEST_NAME not in names:
        raise BundleFormatError(MANIFEST_ABSENT)
    total = 0
    for entry in entries:
        if entry.filename != BUNDLE_MANIFEST_NAME:
            _require_member_path(entry.filename)
        if entry.compress_type != zipfile.ZIP_STORED:
            raise BundleFormatError(UNSUPPORTED_COMPRESSION)
        if entry.flag_bits & _ENCRYPTED_FLAG:
            raise BundleFormatError(ENCRYPTED_MEMBER)
        # Type bits of zero mean the producer recorded no Unix mode at all, which is
        # normal for a DOS-created entry; anything else has to be a regular file.
        if stat.S_IFMT(entry.external_attr >> 16) not in (0, stat.S_IFREG):
            raise BundleFormatError(NON_REGULAR_MEMBER)
        if entry.file_size > MAX_BUNDLE_BYTES:
            raise BundleFormatError(MEMBER_TOO_LARGE)
        total += entry.file_size
    if total > MAX_BUNDLE_BYTES:
        raise BundleFormatError(TOO_LARGE)


def _declared_members(manifest: bytes) -> dict[str, dict[str, Any]]:
    """Return the manifest's member table, keyed by path."""
    try:
        document = json.loads(manifest)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise BundleFormatError(MANIFEST_INVALID) from None
    if not isinstance(document, dict) or not _is_whole_number(document.get("format_version")):
        raise BundleFormatError(MANIFEST_INVALID)
    if document["format_version"] != BUNDLE_FORMAT_VERSION:
        raise BundleFormatError(MANIFEST_INVALID)
    declared = document.get("members")
    if not isinstance(declared, list):
        raise BundleFormatError(MANIFEST_INVALID)
    # Bounded before the table below exists, for the same reason the entry count is.
    if len(declared) > MAX_BUNDLE_MEMBERS:
        raise BundleFormatError(TOO_MANY_MEMBERS)
    table: dict[str, dict[str, Any]] = {}
    for member in declared:
        if (
            not isinstance(member, dict)
            or not isinstance(member.get("path"), str)
            or not _is_whole_number(member.get("size"))
            or not isinstance(member.get("sha256"), str)
        ):
            raise BundleFormatError(MANIFEST_INVALID)
        table[member["path"]] = member
    if len(table) != len(declared):
        raise BundleFormatError(MANIFEST_INVALID)
    return table


def _is_whole_number(value: Any) -> bool:
    """`True` only for a non-negative plain integer.

    `bool` subclasses `int` and JSON `true` equals 1, so an `isinstance` test alone would
    let a Boolean stand in for a version or a length. A negative length is never a length.
    """
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _verified(payload: bytes, declared: Mapping[str, Any]) -> bytes:
    if len(payload) != declared["size"] or sha256(payload).hexdigest() != declared["sha256"]:
        raise BundleFormatError(MEMBER_INTEGRITY_FAILED)
    return payload
