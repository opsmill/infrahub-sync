Added deterministic candidate-kit production. `invoke release.kit` writes the deployment
bundle `infrahub-sync-compose-<version>.tar.gz` and its SHA-256, plus a separate
clean-host qualification kit. Two runs from one tree produce the same archive bytes:
entries are sorted, the owner is numeric and unnamed, the mode is the one Git records
rather than whatever the checkout shows, and both the tar entries and the gzip header
carry the source commit's own time. The task refuses to run while `deploy/compose/`
differs from `HEAD`, so the revision a record names is the content it archived.

The archive ships the five files a deployment needs and nothing a deployment generates on
its host — no credential, no operator environment, no instance identity, and no test
fixture. `invoke release.qualify` writes one record linking the version, the source
revision, the OCI index, each platform's manifest and configuration, the bundle checksum,
the bills of materials, the vulnerability decision, and the gates that ran. It requires
the service artifact record its caller writes — the identifiers, digests, and retention
the artifact service returned for each upload, and the candidate those uploads describe —
and refuses a candidate that qualified nothing or an artifact record naming a different
candidate.

The image gate is that caller on a run whose head is in this repository. It writes the
artifact record from what each upload returned, and both that record and the
qualification record are transient: the run deletes them with the artifacts they
describe. A pull request from a fork takes the route that hands nothing between jobs and
writes neither. Retaining a candidate for approval is a later change.

Bills of materials and vulnerability reports are now named from the release identity, so
a report downloaded on its own says which release and which platform it describes.
