Added deterministic candidate-kit production. `invoke release.kit` writes the deployment
bundle `infrahub-sync-compose-<version>.tar.gz` and its SHA-256, plus a separate
clean-host qualification kit. Two runs from one tree produce the same archive bytes:
entries are sorted, the owner is numeric and unnamed, the mode is the one Git records
rather than whatever the checkout shows, and both the tar entries and the gzip header
carry the source commit's own time. The task refuses to run while `deploy/compose/`
differs from `HEAD`, so the revision a record names is the content it archived.

The archive ships the five files a deployment needs and nothing a deployment generates on
its host — no credential, no operator environment, no instance identity, and no test
fixture. `invoke release.qualify` then writes one record linking the version, the source
revision, the OCI index, each platform's manifest and configuration, the bundle checksum,
the bills of materials, the vulnerability decision, and the gates that ran, together with
the identifiers and digests the artifact service returned. It refuses a candidate that
qualified nothing. On a pull request that record and the `.release/artifacts.json` it
reads are transient: they exist for the clean-host handoff, and the run deletes them with
the artifacts they describe.

Bills of materials and vulnerability reports are now named from the release identity, so
a report downloaded on its own says which release and which platform it describes.
