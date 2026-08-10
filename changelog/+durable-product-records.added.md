Added a typed durable product-record and immutable-artifact provider contract, with
SQLite/filesystem and PostgreSQL/S3-compatible profiles, stable Sync run lookup,
purpose-labelled Prefect execution links, atomic manifest-last publication, and
redaction before persistence. Interrupted publications can be resumed only with matching
redacted content and metadata.
