# Extraction Record

**Extracted on**: 2026-11-14
**Extracted by**: speckit.opsmill.extract

## ADRs Created

None. `dev/adr/0026-the-compose-deployment-takes-an-external-store-credentials-and-ca-from-env.md`
was written from R10 while the spec was implemented, and records R1 to R4. Its status is
Accepted. Its Source line now names the archive path.

## Knowledge Updated

- `docs/docs/development/knowledge/repository-tour.md`, section "The Sync service and its
  worker": `bootstrap.py` and `trust_store.py` in the module list, and a list on how the Compose
  deployment reaches its object store and trusts a private CA (from R1, R2 and R4)
- `docs/docs/development/knowledge/repository-tour.md`, section "The development stack and the
  deployment file": the images named by digest and the tests that check them (from R8)
- `docs/docs/development/knowledge/image-publishing.md`, section "Release path and `latest`":
  the `record_image` job and the token it needs (from R7)
- `docs/docs/development/guidelines/testing-tiers.md`, section "Compose (opt-in)": the two
  architectures of the nightly run, and how to repeat the suite (from R6)
- `docs/docs/development/knowledge/orchestration-prefect.md`, section "Prefect-specific traps":
  importing Prefect configures the root logger (from the implementation notes of R2 and R4)
- `docs/docs/compose-deployment.mdx`, section "Trust a private certificate authority": a trust
  variable set to an empty value counts as unset (read in
  `infrahub_sync/service/trust_store.py`)

## Guidelines Updated

- `docs/docs/development/guidelines/secret-redaction.md`: the row "Compose start-up" in the
  boundary table (from R5 and `contracts/bootstrap-and-startup.md`)
- `docs/docs/development/guidelines/writing-an-adapter.md`, section "Handle optional
  dependencies and credentials": the places to change when an adapter gains a credential
  setting, and the test that checks each place (from R3)
- `docs/docs/development/guidelines/testing.md`: new section "Pin a code constant to the
  deployment file it mirrors". The rule comes from R5 of spec 006
  (`012-v2-configuration-conversion/research.md`). It is written with this extraction because
  spec 015 changed the constant that the rule's example names.

## Changed from the extraction plan

Each statement was read in the code on 2026-11-14 before it was written. These statements of
the plan were narrowed:

- The plan said that `install_custom_ca_bundle()` is the first call of `bootstrap.main()`,
  `serve.main()` and `worker.main()`. In `serve.py` the call is in `build_app()`, and in
  `bootstrap.main()` it follows the logging set-up. The page says that the three call it before
  they build any client.
- The plan said that `sync-bootstrap`, `sync-api` and `sync-worker` call
  `storage.s3_client_arguments`. Only `bootstrap.py` calls it directly; the two others call it
  through `storage.service_product_projection`.
- The plan said that `bootstrap.py` compares the endpoint with
  `INFRAHUB_SYNC_BUNDLED_OBJECT_STORE`. `check_store_settings` does not read that setting: it
  tests whether the host name of the bundled store resolves.
- The plan gave a reason why an empty trust variable counts as unset (botocore refuses an empty
  `AWS_CA_BUNDLE`). No file of the repository states that reason, so the pages give the
  behaviour without it.
- The plan said that a trust store error names the file and the error class. The error for a
  private key names the file only.
- The plan's guideline for a new credential setting named four places. The guideline names
  six, and says which test reads each one.
- The plan proposed an extraction comment above the boundary table and one in the adapter
  guideline. Neither was added: the extraction of spec 007 added its section to
  `secret-redaction.md` without one.

## Not extracted

- R1 to R4 (the external store settings, the bootstrap checks, the fixed list of credential
  variables, the CA directory) and their alternatives: already ADR 0026.
- The `.env` settings, the bootstrap failure names, the S3 operations Sync relies on, and which
  service receives which credential: already in `docs/docs/compose-deployment.mdx`.
- The credential variables of each adapter: already in `docs/docs/compose-deployment.mdx`
  ("Credential variables the worker receives") and
  `docs/docs/adapters/choosing-an-adapter.mdx`.
- The CA rules for operators (PEM text only, `*.pem` and `*.crt`, the refusal of a private key,
  user ID 10001, the restart after a change, the key identifiers under Python 3.13): already in
  `docs/docs/compose-deployment.mdx` and `docs/docs/custom-certificates.mdx`.
- How the trust store scans a file for a private key, and that it removes a bundle whose write
  failed: held by `infrahub_sync/service/trust_store.py` and its comments.
- Host-mode development with an external object store or a CA directory (the fix of issue
  #414): already in `docs/docs/development-stack.mdx`.
- R6, the candidate causes C1 to C4 of a lifecycle failure: hypotheses that are not measured.
  They stay in `research.md`, and issue #420 tracks the measurement.
- R7, the part for users (the release notes name the image by digest): already in
  `docs/docs/container-image.mdx` and `docs/docs/development/guides/publishing-an-image.md`.
- R9 (the packaged copy of the root Compose file is byte-equal): already in
  `docs/docs/development/knowledge/repository-tour.md`.
- R10, R11, `plan.md`, `quickstart.md`, `tasks.md`, `alignment-check.md`,
  `opsmill-implement-report.md`, `checklists/`, `critiques/` and `reviews/`: execution records.
- `data-model.md`: covered by ADR 0026, `docs/docs/compose-deployment.mdx` and the update of
  `repository-tour.md`.

## Where the spec files and the merged code differ

The code is right in each case:

- R1 names an S3 client builder `build_s3_client`. The code has `s3_client_arguments` in
  `infrahub_sync/service/storage.py`, and no `build_s3_client`.
- R4, step 5, says the trust store logs through `structlog`. `trust_store.py` uses the standard
  `logging` module, as the implementation notes of R4 say.
- R4, step 4, says a trust variable is kept when it is already set. The code keeps it only when
  it holds a value; an empty value counts as unset.
- The last implementation note of R4 says the documentation does not mention the authority key
  identifier. `docs/docs/compose-deployment.mdx` now states it.
- R6, "Lifecycle failure causes", says "Not measured yet". That was the state at the merge.
- `tests/configuration/test_conversion_worker_variables.py` cites "research R5". That is R5 of
  spec 006, not of this spec.

## Open at archive time

Open tasks of `tasks.md`:

- T033, partly done, not tracked. The test serves the external store, Infrahub and a NetBox
  stand-in over TLS from a test CA, and seven adapters' clients were checked. The clients of the
  `ipfabricsync` and `slurpitsync` adapters were not checked, because their vendor libraries are
  not installed in the image. Whether those libraries read the CA bundle is not known. Issue
  #420 does not name this task. `docs/docs/compose-deployment.mdx` and
  `docs/docs/custom-certificates.mdx` state the limit.
- T040, T041 and T042: tracked by issue #420, open on 2026-11-14. They run the Compose lifecycle
  suite 10 times on each of amd64 and arm64, fix each cause found, and run it 10 more times
  (SC-006). Pull request #413 merged without them. The implementation report says that whether
  the `ubuntu-24.04-arm` runner can run the suite is not known; the same issue covers it.

Issue #414 (host-mode `invoke dev.deps` ignored an external object store and the CA directory)
was fixed by pull request #413 and is closed.

Found during the extraction, not tracked:

- No test compares the credential variables of the `sync-worker` service in
  `development/docker-compose.yml` with those of the root `docker-compose.yml`. The two lists
  were equal on 2026-11-14 (24 variables each). With one variable removed from the development
  file, `tests/dev_stack` (without the smoke tests), `tests/compose/test_compose_contract.py`,
  `tests/compose/test_documentation.py`, `tests/configuration`, `tests/test_nightly_e2e.py`,
  `tests/test_workflow_contracts.py` and `tests/testcontainers/test_packaging.py` all passed,
  with no test skipped. The adapter guideline says to change this list by hand.
- `test_the_page_documents_every_variable_the_compose_file_reads` checks that
  `docs/docs/compose-deployment.mdx` names each variable somewhere in the page. No test checks
  the table "Credential variables the worker receives" itself.

Findings that `opsmill-implement-report.md` ("Review findings") leaves as they are, none
tracked:

- `test_only_the_newest_stable_release_is_called_newest` in `tests/test_workflow_contracts.py`
  failed several times while Docker builds ran and passed on every run after. Not changed. It
  passed in the run of 2026-11-14 described above.
- Compose starts `sync-bootstrap` when the bundled store fails to start, because leaving the
  store out depends on that. `docs/docs/compose-deployment.mdx` says how to tell the two cases
  apart. Still present on 2026-11-14.
- A CA path that is not a directory logs a warning and does not stop the service. Still present
  on 2026-11-14 (`install_custom_ca_bundle`).
- Compose cannot limit `INFRAHUB_SYNC_BUNDLED_OBJECT_STORE` to 0 or 1. Not checked again.
- `tasks/dev.py` copies the container's CA path into host-mode processes. Not checked again;
  pull request #413 changed how `tasks/dev.py` maps the CA directory for issue #414.
- `tests/integration/test_managed_write_guard_live.py` failed 2 of 4 cases on the development
  stack while spec 008 "PR 2" was implemented in the same session. Not investigated then, and
  not checked again.

Suggestions of `reviews/review-run-20261113.md` that the file does not mark as fixed, none
tracked:

- `record_image` also ran for a manual publishing run from a branch. Fixed since: on
  2026-11-14 the job runs only when the reference is a tag.
- A wildcard DNS search domain can make the host name `object-store` resolve while the bundled
  store is left out. Not checked again.
- On a bucket with versioning, the delete of the check key leaves an older version of the
  object. Not checked again.
- A temporary DNS failure counts as "the bundled store does not run", and a 301 or 400 answer to
  `HeadBucket` gives `object-store-unavailable` with no hint. Not checked again.
- ADR 0026 said four more S3 requests for each start. Fixed since: the ADR now says three when
  the bucket exists and four when it does not. The other three wording findings of the same row
  (the `HeadBucket` refusal, the comment in the Compose file, the certificate count logged at
  each start) were not checked again.
- The failure names are plain strings, `check_create_only` takes `Any`, and
  `s3_client_arguments` returns `dict[str, Any]`. Still present on 2026-11-14 for
  `s3_client_arguments` and `check_create_only`; the failure names were not checked again.
- The expression for the key prefix appears in both `bootstrap.py` and `storage.py`. Not checked
  again.
- The three entry points report a `TrustStoreError` in three different ways. Still present on
  2026-11-14: `bootstrap.main()` logs it, `worker.main()` writes it to standard error, and
  `serve.build_app()` raises it.
- More tests were suggested: more private key formats, a directory and a file that cannot be
  read, an endpoint that is not set while the bundled store is left out, and a `BotoCoreError`
  from `HeadBucket`. Not checked again.

## Archive

The spec directory moved to `dev/specs/archive/015-compose-store-credentials-ca/` as a
historical record.
