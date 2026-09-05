"""Search retained build output for a value that must not be in it.

Stated as a search over every byte of every file, compressed layers included,
rather than as a list of the places a secret is known to have leaked before. The
containment claim is only worth the surfaces it actually covers.
"""

from __future__ import annotations

import gzip
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

CHUNK_BYTES = 1 << 20
_GZIP_MAGIC = b"\x1f\x8b"


class ByteStream(Protocol):
    """The one thing the search needs from a plain file and from a gzip member."""

    def read(self, size: int = ..., /) -> bytes: ...


def _stream_holds(stream: ByteStream, needle: bytes) -> bool:
    """Search a stream without holding it in memory, across chunk boundaries."""
    overlap = len(needle) - 1
    tail = b""
    while chunk := stream.read(CHUNK_BYTES):
        if needle in tail + chunk:
            return True
        tail = (tail + chunk)[-overlap:] if overlap else b""
    return False


def file_holds(path: Path, needle: bytes) -> bool:
    """Report whether a file holds the value, stored plainly or gzip-compressed."""
    with path.open("rb") as handle:
        if _stream_holds(handle, needle):
            return True
        handle.seek(0)
        if handle.read(len(_GZIP_MAGIC)) != _GZIP_MAGIC:
            return False
    with gzip.open(path, "rb") as decompressed:
        try:
            return _stream_holds(decompressed, needle)
        except (OSError, EOFError):
            return False


def canary_locations(value: str, roots: Iterable[Path]) -> tuple[str, ...]:
    """Return every file under the given roots whose bytes hold the value."""
    needle = value.encode()
    return tuple(
        str(path)
        for root in roots
        for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file())
        if file_holds(path, needle)
    )
