# Configuration route boundary

The configuration-route change set was reviewed against `b52485a`.

It adds only managed configuration registration, immutable-version reads and validation,
their request grammar, pagination, idempotency receipts, and secret-safe audit evidence.
It does not add a CLI or Python client, server-version endpoint, client-version-range
refusal, Prefect parameter, worker resolver, storage-profile selection, or a new redaction
form.
