"""Everything a repeat start or a restart must leave equal, read from the stores.

Deliberately not the deployment's own account of itself: a restart that lost
durable state and an API that failed to notice look identical from the API. So
this reads what PostgreSQL holds and what the object store holds, and prints a
stable digest of both for the driver to compare across an operation.

Contents, not counts. A deployment that replaced every record with a fresh one
of its own, or rewrote an object under the key it already had, keeps every row
count and every object key -- so counts and keys are the two things a lost-state
bug is most likely to preserve. Each table contributes a digest over its rows and
each object a digest over its body, so a value that moved is a snapshot that
moved.

Two digests, because a record and a body are different shapes. A record is a
sequence of values, so its digest frames each value's length: without that, a
value moved across a column boundary would hash the same. A body is one value
arriving in however many pieces the transfer chose, so its digest is raw --
framing the read's own boundaries would move the snapshot for an object nobody
touched.

Nothing read is printed. A digest is not the value it covers, and these records
hold a registered package's declared content while the objects hold what a run
planned against a destination. What reaches the driver is the deployment's own
table and object names, the counts, and the digests.

The table set is discovered rather than named, so a schema change does not
quietly narrow what is being compared. The columns are discovered the same way,
so a new column is compared without this check being told about it.
"""

from __future__ import annotations

import hashlib
import os
import sys
from collections.abc import Iterable, Sequence

# Columns a live deployment moves on its own, per table, each with the reason it
# moves them. A column exclusion narrows one value and leaves the rest of the
# record compared, which is why there are no table exclusions here.
VOLATILE_COLUMNS: dict[str, tuple[str, ...]] = {
    # `LivenessSweeper.reconcile_execution` re-observes an execution that has not
    # reached a verdict, on a cadence of its own, and writes exactly these two
    # columns. An operation this gate brackets can legitimately span one of those
    # observations. Every other column on the table -- the two identifiers, the
    # purpose, the attempt, the position, the claim, the stall, the cancellation
    # fields and the terminal verdict -- is written by the run itself and stays
    # compared.
    #
    # A column renamed upstream drops out of this list silently, and the effect
    # is that it starts being compared. Failing towards comparing more is the
    # direction this check is allowed to be wrong in.
    "prefect_executions": ("last_observed_at", "last_observed_state"),
}

# One megabyte at a time. A body is read to be digested and never held whole.
BODY_CHUNK_BYTES = 1 << 20


def digest(parts: Iterable[bytes]) -> str:
    """A digest over a sequence of byte strings, each one length-delimited.

    Length-delimited because the parts are fed to one hash in sequence. Without
    it, a character moved from the end of one part to the start of the next
    digests equal -- and a record rewritten that way is exactly what this
    snapshot exists to see.

    For a sequence of values only. An object's body is one value that arrives in
    however many pieces the transfer chose, and `body_digest` covers that.
    """
    running = hashlib.sha256()
    for part in parts:
        running.update(f"{len(part)}:".encode())
        running.update(part)
    return running.hexdigest()


def rendered(value: object) -> bytes:
    """One column value as the bytes its digest covers.

    Tagged rather than stringified, so a null column is not the empty string and
    not any text that spells it: a column holding nothing and a column holding
    "None" are different records. Every column these tables declare is text, an
    integer or a boolean, so `str` is a faithful rendering of the rest.
    """
    return b"" if value is None else b"\x01" + str(value).encode("utf-8")


def table_lines(table: str, rows: Sequence[Sequence[object]]) -> list[str]:
    """The two lines one table contributes: how many records it holds, and what they are.

    Two lines rather than one because the driver reads the count on its own, to
    prove there is state for an equality to be about before it makes one. A
    count with a digest appended is not a count.

    The row digests are sorted rather than the rows: no table here is read with
    an ORDER BY it is guaranteed a unique key for, so the order the server
    returned them in must not reach the snapshot.
    """
    digests = sorted(digest(rendered(value) for value in row) for row in rows)
    return [f"table {table} {len(digests)}", f"contents {table} {digest(item.encode() for item in digests)}"]


def body_digest(chunks: Iterable[bytes]) -> str:
    """A digest over an object's bytes, indifferent to how the read was chunked.

    Raw rather than length-delimited. `StreamingBody.read(n)` returns *at most*
    n bytes -- urllib3 hands over what the socket and the decoder gave it -- so
    the boundaries belong to the transfer and not to the object, and two reads
    of one unchanged body can be split differently. Framing those lengths would
    move the snapshot for a deployment that changed nothing, and every row
    comparing one would fail.

    Safe to hash raw for the same reason it would be unsafe for a record: a body
    is one value, so there is no boundary between values to lose.
    """
    running = hashlib.sha256()
    for chunk in chunks:
        running.update(chunk)
    return running.hexdigest()


def object_line(key: str, body: Iterable[bytes]) -> str:
    """The one line one stored object contributes: its key and the digest of its body."""
    return f"object {key} {body_digest(body)}"


def main() -> None:
    """Read both stores and print the snapshot the driver compares across an operation.

    The store drivers are imported here rather than beside the others: the three
    renderings above decide what the snapshot distinguishes, and they are driven
    directly by a test that runs where these optional service dependencies are
    not installed.
    """
    import boto3  # ty: ignore[unresolved-import] - TODO: optional service dependency
    import psycopg  # ty: ignore[unresolved-import] - TODO: optional service dependency
    from psycopg import sql  # ty: ignore[unresolved-import] - TODO: optional service dependency

    lines: list[str] = []
    with psycopg.connect(os.environ["INFRAHUB_SYNC_DATABASE_URL"]) as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' ORDER BY table_name"
        )
        for (table,) in cursor.fetchall():
            cursor.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = %s ORDER BY column_name",
                (table,),
            )
            stable = [name for (name,) in cursor.fetchall() if name not in VOLATILE_COLUMNS.get(table, ())]
            # The one thing an exclusion may not do. A table with nothing left to
            # compare would report equal for every deployment there is.
            if not stable:
                sys.exit(f"the exclusions leave {table} with no column to compare")
            cursor.execute(
                sql.SQL("SELECT {} FROM {}").format(
                    sql.SQL(", ").join(sql.Identifier(name) for name in stable), sql.Identifier(table)
                )
            )
            lines.extend(table_lines(table, cursor.fetchall()))

    store = boto3.client("s3", endpoint_url=os.environ["INFRAHUB_SYNC_S3_ENDPOINT_URL"])
    bucket = os.environ["INFRAHUB_SYNC_S3_BUCKET"]
    pages = store.get_paginator("list_objects_v2").paginate(Bucket=bucket)
    for key in sorted(item["Key"] for page in pages for item in page.get("Contents", [])):
        with store.get_object(Bucket=bucket, Key=key)["Body"] as body:
            lines.append(object_line(key, body.iter_chunks(chunk_size=BODY_CHUNK_BYTES)))

    print("\n".join(lines))


if __name__ == "__main__":
    main()
