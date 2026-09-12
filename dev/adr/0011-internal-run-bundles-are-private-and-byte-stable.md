# 11. Internal run bundles are private, uncompressed, and never redacted

**Status**: Accepted
**Date**: 2026-09-03
**Source**: PH-3 run-bundles unit 2

## Context

Workers were about to stop sharing a filesystem, so a stage has to hand its successor the
files it produced through storage both of them can reach. The product already has such a
place: run-owned artifacts, in PostgreSQL and S3-compatible object storage. Reusing it
raised three questions the existing artifact contract had no answer for.

The first is who may read one. Every artifact was public: the run resource enumerated it,
the list route returned it, and the single-ID route served its bytes. A stage's internal
state is not operator-facing content, and the identifier a stage looks it up by is fixed
by the protocol, so it is guessable by anyone who reads the source.

The second is what a bundle costs to read. Both providers materialized whole objects —
`Path.read_bytes()` and a byte-returning `get()` — so the only thing bounding a read was
that the product had written the object in the first place.

The third only appears once the container is binary. `publish_artifact` redacts supplied
secret byte sequences from artifact bytes before computing the digest. Applied to an
archive, that rewrites the file: the bytes change, the digest changes, and it happens only
when a configured secret sequence happens to occur inside compressed Parquet. Byte-for-byte
determinism is what makes a fixed checkpoint identifier safe to retry, and this would break
it non-reproducibly, on some runs and not others.

## Decision

An artifact carries a visibility, and internal is a real boundary rather than a naming
convention. The public run projection and the public single-ID lookup filter on it in SQL,
so a guessed identifier fails identically to one that was never published. The public wire
contract does not declare the field, and the list resource is built through the bounded
twin that drops what the contract does not declare, so the distinction cannot leak by
being projected.

Internal bytes are never passed through redaction. An internal bundle is never returned to
a client, its only readers are the product's own stages, and its integrity is established
by stored size and SHA-256 before anything parses it. Redacting inside a binary container
protects nothing readable while corrupting the archive. Public artifacts keep redaction
exactly as they had it.

Bundle members are stored uncompressed. zlib output is not guaranteed identical across
zlib builds or Python versions, so a compressed bundle can verify on the host that wrote it
and fail on the host that reads it. The members are Parquet and JSONL, and Parquet is
already compressed internally, so compression buys almost nothing against a 64 MiB bound.
Storing members uncompressed also makes the archive-level bound bound the members, which is
what lets a decompression bomb be refused on its method rather than on its declared size.

Everything else about the container is pinned for the same reason: fixed timestamps, fixed
permissions, a fixed host-system byte, fixed member order, and a canonical manifest. A
retry at a fixed checkpoint identifier then either presents the same digest and is the same
publication, or presents a different one and is a conflict.

Reading an internal artifact is bounded three times, because the three fail differently.
The committed reference records the size, so an oversized artifact is refused before the
provider is contacted. The provider then checks stored length from metadata before
transferring. The transfer itself reads at most one byte past the bound, because metadata
comes from the same store as the object and cannot be its own witness. Only then are stored
size and digest verified, and only then may anything parse the archive.

An archive read back from storage is untrusted input. It is validated as a whole — member
count, grammar, duplicate and case-colliding names, entry types, compression method,
encryption, declared against present members, manifest usability, and size — before a single
byte reaches disk, and extraction populates a private directory that is renamed into place,
so a destination either holds the whole bundle or does not exist. The writer holds itself to
the same accepted domain, so an archive it returns is always one the reader takes back.

## Consequences

A stage can hand its successor state across hosts with no shared filesystem, and can prove
it received exactly what was sent.

The artifact contract now has two classes rather than one. Anything added to the artifact
record has to state which class it belongs to, and any new public artifact surface has to
filter visibility in its query — filtering in the response model is not sufficient, because
the single-ID route resolves the reference and body directly once it finds it on the run.

The bundle format has a version in its name and in its manifest. There is no reader for any
other version and none will be added; a format change is a new version, and the pre-release
boundary means old bundles are discarded rather than migrated.

The 64 MiB and 1024-member bounds are product limits, not tuning parameters. A configuration
whose plan checkpoint would exceed either cannot hand off, and that is a refusal rather than
a silent truncation. The two bounds answer different questions: bytes bound the transfer,
and the member count bounds the work of reading an archive that is small but numerous.

## Alternatives considered

**Keep every artifact public and rely on the identifiers being obscure.** The checkpoint
identifiers are fixed by the protocol and readable in the source, so this is not a control.

**Redact internal bundles like public artifacts.** This is the behaviour that forced the
decision. It corrupts the archive rather than protecting it, and makes determinism depend on
whether a secret sequence happens to appear in compressed Parquet.

**Compress members.** Rejected because determinism across hosts is a required property and
zlib output is not stable across builds, and because Parquet is already compressed.

**Bound reads only at the projection, from the recorded size.** The recorded size is a claim
about the store made by the same store; it cannot witness itself. The streamed bound is what
makes the claim checkable.
