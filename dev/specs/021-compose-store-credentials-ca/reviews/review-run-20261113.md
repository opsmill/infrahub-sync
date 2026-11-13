# Review of spec 021 (d442d27b..HEAD), 2026-11-13

Scope: the spec 021 commits only, `dev/specs/` left out. The change-detection script compares the
branch with `main`, which would include all of `feature/v3-develop`, so the review used the spec's
base commit instead. Six aspects ran: code, comments, tests, errors, types, simplify. No review
configuration file exists, so every aspect was on.

## Critical issues

- tests: a failed listing in the live external-store tests counts as an empty bucket.
  `ExternalStore.keys()` returns `None` and the callers use `or []`, so the check that no check key
  remains passes without listing anything (`tests/compose/test_external_store.py`, uses near lines
  309, 314, 338, 365, 476). **Fixed:** `keys()` fails the test when the listing fails; the bucket
  creation wait uses a separate `listing()` that may return None.

## Important issues

- errors: `create_bucket` and the create-only `put` report a 403 as `object-store-unavailable`
  instead of `object-store-access-refused` (`infrahub_sync/service/bootstrap.py`, `converge_bucket`
  and `check_create_only`). **Fixed:** one helper maps 403 and 501 for all three requests; tests added.
- errors: a store that answers the conditional write with 501 `NotImplemented` is reported as
  `object-store-unavailable` instead of `object-store-unsupported` (`bootstrap.py`,
  `check_create_only`). **Fixed** with the same helper.
- errors, code: `install_custom_ca_bundle` turns no `OSError` from writing the bundle into
  `TrustStoreError`, so a full or missing scratch directory ends with a traceback
  (`infrahub_sync/service/trust_store.py:106-113`). **Fixed:** both reads and the write raise
  `TrustStoreError`, naming the directory only; tests added.
- errors: `INFRAHUB_SYNC_CA_DIRECTORY` set to a path that is not a directory is ignored with no log
  line (`trust_store.py:92`). **Fixed:** a warning names the setting and the path. Not a refusal,
  because the Compose default always mounts a directory there.
- types: `except ValueError` in `_converge` reports every refused S3 setting, including the
  addressing style and botocore's `InvalidRegionError`, as `object-store-endpoint-invalid`
  (`bootstrap.py:248`). **Fixed:** the endpoint raises `EndpointSettingError`; any other refused
  setting gives the new family `object-store-setting-invalid`, documented in the docs and the contract.
- comments: the docs say the CA directory takes `*.pem` or `*.crt` files, but only PEM text is
  accepted; DER files are refused and other extensions are skipped without a message
  (`docs/docs/compose-deployment.mdx`, `docs/docs/custom-certificates.mdx`). **Fixed.**
- comments: the news fragment says the `cosign` check accepts only the release workflow's
  signature; the identity matches any run of `ci-docker-image.yml` at any tag
  (`changelog/+compose-store-credentials-ca.added.md`). **Fixed:** the fragment now says a
  signature made by the image workflow when it runs for a tag.
- tests: no test checks that `serve`, `worker` and `bootstrap` call `install_custom_ca_bundle()`
  before building clients and exit non-zero on `TrustStoreError`. **Fixed:**
  `tests/service/test_trust_store_entry_points.py`.
- tests: the TLS store test has no negative case (a run without the CA that must fail). **Open**,
  with T033.
- tests: the leak test runs no plan, so no adapter credential is resolved before the logs are
  searched; the S3 access key gets no generated value. **Open**, with T033.
- tests: the cleanup branch of `check_create_only` (delete fails) has no test. **Fixed.**

## Suggestions

- code: `record_image` also runs on a manual publish from a branch, where the tag-based `cosign`
  identity in the release notes would not match; add `github.ref_type == 'tag'`
  (`.github/workflows/workflow-publish.yml`).
- code: the bundle path is fixed, so two host-mode processes rewrite the same file at start; use a
  per-process file (`trust_store.py`). **Fixed:** `mkstemp`.
- code: wildcard DNS search domains can make `object-store` resolve while the bundled store is left
  out (`bootstrap.py`, `check_store_settings`).
- code: on a versioned bucket the delete leaves an older version of the check object.
- code: `scale: ${INFRAHUB_SYNC_BUNDLED_OBJECT_STORE:-1}` accepts only integers; the docs should say
  `0` or `1`.
- errors: a temporary DNS failure counts as "bundled store not running"; a 301 or 400 from
  `HeadBucket` (region mismatch) gives `object-store-unavailable` with no hint.
- comments: ADR 0026 says four more S3 requests per start; it is three more with the bucket present
  and four with it absent. The docs say a `HeadBucket` refusal gives `object-store-access-refused`;
  only a 403 does. The compose comment says bootstrap refuses either setting without the other; it
  checks only the endpoint. The docs say each service logs a certificate count at every start; with
  no file it logs "no CA certificate file", and with no directory nothing.
- types: failure families are plain strings; `check_create_only` takes `Any`; `s3_client_arguments`
  returns `dict[str, Any]`.
- simplify: the key prefix expression appears in both `bootstrap.py` and `storage.py`; the
  create-only key expression repeats `uuid.uuid4()`; the three entry points report
  `TrustStoreError` in three different ways; `"404"` in `_BUCKET_ABSENT_CODES` duplicates the
  status check. **Fixed:** the create-only key and `"404"`; the others are open.
- tests: more private-key formats, a real unreadable directory, an unreadable file, an unset
  endpoint with the bundled store left out, and a `BotoCoreError` from `HeadBucket`.

## Strengths

- Refusal messages name the setting or file and never its value; the unit tests check this.
- The trust-store tests check exact log lines, root order in the bundle and file mode 0600.
- The contract tests check credential receivers, the CA mount and image digests against the
  adapter registry.
- `contents: write` reaches only the `record_image` job.

## What happens next

Every forked `/code-review xhigh --fix` run stopped on an authentication error, so the findings
above were fixed by hand. Suggestions not marked fixed are open.
