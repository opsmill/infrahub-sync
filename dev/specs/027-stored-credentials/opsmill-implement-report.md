# Implementation report: spec 027, stored credentials

Status: INCOMPLETE. 72 of 81 tasks are done. The other 9 wait for a decision by the maintainer or
need the development stack (listed under "Not done").

- Branch `027-stored-credentials`, in the worktree `.worktrees/027-stored-credentials`.
- Base commit `e73deca4`, head commit `6b2ebd02` before this report: 80 commits. Nothing is pushed
  and no pull request is open.
- The run started on 2026-11-20 in the evening and ended on 2026-11-23. It stopped three times on usage
  limits and was resumed each time.
- The full unit tier at `491979a4` (the last code commit): 11521 passed, 0 failed.

Sources: [spec.md](spec.md), [tasks.md](tasks.md), [overnight-log.md](overnight-log.md) (each advisor
question, its answer and what was done next), [notes.md](notes.md) (the prototypes).

## 1. Done

- **Storing and releasing credentials (US1).** An administrator creates, changes and describes a
  credential through the Sync API, the CLI group `infrahub-sync credentials`, and (prototype only) the
  web interface. The Sync API stores the value encrypted with AES-256-GCM. A configuration package
  names a credential with `credential: <name>` in each adapter block. The worker receives values only
  through `POST /api/worker/runs/{run_id}/credentials`, for a claimed execution in progress.
- **Removal of the old format.** The `credentials` block, `$credential` references, the environment
  provider of the Sync service and the publishing of worker credential variables are removed. A version
  stored by the earlier build is still read when its `credentials` block is empty. A version that used
  the old format for a secret is refused with `422 credential-format-removed`. Its run history stays
  visible.
- **Start-up settings.** The Sync API refuses to start without `INFRAHUB_SYNC_ENCRYPTION_KEY` or
  `INFRAHUB_SYNC_WORKER_TOKEN`, or with a malformed value, and names the variable. The three Compose
  files, `dev.api` and the test helpers provide both.
- **Revisions on runs (US2).** Each run answer includes `credentials_used` (stage, block, name,
  revision). `runs show` prints a "Credentials used" section.
- **Deletion (US4).** `DELETE /credentials/{name}` and `credentials delete NAME [--yes]`. Deletion is
  refused with `409 credential-in-use` while the latest version of a configuration, or a schedule that
  is not deleted, names the credential.
- **Conversion report (US3).** `configs convert` writes `credential:` names and lists each credential
  to create with its command. A registration that names a missing credential lists each missing
  credential and its fields.
- **Records.** ADR 0057 (0039 superseded, 0026 and 0040 partly superseded), constitution 1.3.0,
  `AGENTS.md`, a user guide `docs/docs/guides/credentials.mdx`, the deployment, adapter, upgrade and
  reference pages, the README and two changelog fragments.
- **Web interface, first step.** The queries, actions and error descriptions (T073). Prototypes with
  three versions for each of four screens at `/proto/credentials` (T074).

## 2. Blocked

Nothing was blocked by a critical blocker. Four items wait for a decision that is yours:

- **T074 to T079: choose one prototype version per screen.** Open `/proto/credentials` and record
  the choices in [notes.md](notes.md). Recommended next step: pick, then run T075 to T079. Remove the
  prototype route before the branch is merged; the steps are in `notes.md`.
- **T080: the Helm chart pull request in `opsmill/infrahub-helm`.** It was not opened, because it
  needs your request. Until the chart passes the three new variables, the published chart cannot start
  this image, because the Sync API refuses to start without them. Recommended answer: ask for the
  pull request before this branch is released.
- **T049: the environment reads of `select_runtime_credential`.** The direct Prefect flow
  (`infrahub_sync/orchestration/flow.py:214`, `infrahub_sync/execution.py:785`) builds adapters outside
  a registered package and still reads variables such as `NETBOX_TOKEN`. `spec.md:334` says that flow
  does not change. Removing the reads would break it. Recommended answer: change T049 to keep the
  environment reads for the direct Prefect flow only; a note is under T049 in `tasks.md`.
- **Changelog fragments that describe removed behavior.** Not changed. Recommended at release:
  - delete `+credential-env-prefix.security.md`, `+legacy-credential-run-admission.fixed.md`,
    `+legacy-credential-saved-plans.changed.md` and `+ai-agent-page-source-pin.housekeeping.md`;
  - reword `+validate-branch.added.md`, `+compose-store-credentials-ca.added.md` and
    `+v2-configuration-conversion.added.md`;
  - check `+upgrade-steps.changed.md`.

## 3. Advisor decisions

Each one is written out in [overnight-log.md](overnight-log.md).

- **A1. Start-up replaces the receipt check of an existing table.** Kept. Two limits:
  - an earlier build that starts later adds its old check back;
  - two builds on one database at the same time can leave both checks in place.
- **A2. A scheduled start with branch review reads earlier proposed changes before a run exists.**
  Added the second worker route `POST /api/worker/schedules/{schedule_id}/proposed-change-states`: the
  Sync API reads Infrahub itself and returns the states only.
- **A3. Every earlier version was refused, and T049 stayed open.** The refusal was narrowed. A stored
  version with an empty `credentials` block is read and keeps its stored checksum; a test pins a
  checksum the earlier build wrote. T049 stays open (see "Blocked").
- **A4. The Sync API started without the key, and the marker test failed in the full tier.** The code
  was changed to match the spec: the process refuses to start. The marker test now searches the
  directories Prefect resolves.

## 4. Not done

- T049, T074 to T080: see "Blocked".
- **T081, partly done.** Format, lint, `ruff`, `ty` (31 warnings, exit 0), the CLI help checks and the
  unit tier pass. Scenarios 3 to 7 of [quickstart.md](quickstart.md) need the development stack, which
  another session used during this run, so they were not run.
- **Test tiers that were not run.** They need Docker images built from this checkout or a live stack.
  Only their collection was checked:
  - `uv run pytest -m compose tests/compose` (64 tests)
  - `uv run pytest -m docker tests/compose` (6 tests)
  - `uv run pytest -m docker tests/image` (68 tests)
  - `uv run pytest -m integration tests/integration` (70 tests)
  - `uv run invoke dev.smoke` (25 tests)
- **The review skill `speckit-review-run`** was no longer available in the session. A single review
  agent (Opus) reviewed the diff instead; its findings are in section 5.
- **`emil-ui-review`** was not run on the prototypes.

## 5. Risks

Review findings (none high; neither was fixed):

| Severity | File | Finding |
| --- | --- | --- |
| Medium | `infrahub_sync/product_store/store.py:3784`, `infrahub_sync/service/schedules.py:419`, `:497` | Creating a schedule, or changing its pinned version, does not check that the version's credentials exist and takes no credential lock. A schedule can pin an older version whose credential was deleted; each start then fails with `credential-not-found`. A credential created later with the same name is then used by that schedule. |
| Low | `infrahub_sync/service/credential_actions.py:208` | A value shorter than 6 characters is stored, but the redaction registry ignores values under 6 characters (`execution.py:131`). An adapter error that quotes such a value would show it in the run log. |

Recommended answer for both: check the credentials and take the lock when a schedule is created or
re-pinned; refuse values shorter than 6 characters at creation, or document the limit.

Other risks:

- **Upgrade.** Every version that used a secret through the old format must be registered again with
  `credential:` names. Its schedules fail at each start, and its saved plans cannot be approved or
  applied, until then. Registering the same file again creates a new version with a new checksum.
- **A run start takes no credential lock.** A deletion during a run start lets the run be accepted,
  then refused at release with `422`. No value is released.
- **Receipt check at start-up (A1).** An earlier build started later on the same database probably
  fails on PostgreSQL once a credential receipt exists. The alternative is to refuse an earlier
  database at start-up.
- **The worker boundary has two routes (A2).** The second route reads Infrahub on `main` with a stored
  token. Its read can take up to 60 seconds, while the worker client waits 10 seconds, 3 times.
- **The worker starts without its settings.** It does not refuse to start. Each run that names a
  stored credential then fails, with a cause that names both variables.
- **The Helm chart.** The published chart cannot start this image until T080.
- **The key cannot be changed, and losing it means re-entering every credential.** This is documented.
- **Tests written after their code.** Chunks 8, 8b, 12, 14, 18 and 19 wrote some tests after the code;
  each chunk's ledger line names them.
- **One request reached the other session's API.** During chunk 10, one test sent a GET request to
  `localhost:8000` and received `401`. The test was fixed, and later chunks checked ports first.
- **Not verified here.** The frontend generated types come from an in-process OpenAPI document, not
  from a running API. The web interface has no text for the run failure cause `credential-missing`.
- **`tests/test_benchmark_runner.py`** failed once and passed on the next run, earlier in the run.
- **Not done in this run:** a full `emil-ui-review` of the prototypes; the `adr-index.mdx` page lacks
  ADRs 0051 to 0053 (this was so before the change).

## Suggested next steps

1. Pick the prototype versions in `notes.md`, then run T075 to T079.
2. Decide T049's text and the changelog fragments.
3. Fix the medium review finding on schedules.
4. Build the image and run the Compose, Docker, integration and smoke tiers, and scenarios 3 to 7 of
   `quickstart.md`, on the development stack.
5. Ask for the Helm pull request (T080), then open the pull request for this branch.

## Chunk ledger

One line per chunk: chunk, tasks, outcome, commits, notes.

```text
1|T001-T003|3/0/0|c78f9061|cryptography pin >=50,<51 (lock holds 50.0.2); CredentialSettingsError subclasses AccountSettingsError; worker_settings also refuses userinfo/no-host URL
2|T004-T009|6/0/0|24f78d42|nonce+ciphertext base64 TEXT; encryption via callback inside the store transaction; COLLATE "C" sort; AuditEvent.event_type pattern must be extended for credential events (routes chunk)
3|T010-T013|4/0/0|1414a864|worker token optional until T054 (warning when unset); worker refused on every authenticate route (no path check), require_worker for worker routes; worker name reserved; account_actions.py pylint too-many-lines disable; code written before tests (tests confirmed failing without code); full unit tier not rerun after public-surface fix (covered by chunk 4)
4|T014-T017|4/0/0|7585d583, 225f359e|one CREDENTIAL_NAME_PATTERN in configuration/models.py; credential settings refused at parse (api_key, auth, password, token, username); credential-not-used = validation finding (FROZEN_CODES, doc now 16 codes); unknown auth_method raises UnknownCredentialMethodError (T034 must handle); Slurpit api_key<-token only; checksum unchanged when credential absent
5|T018-T020|3/0/0|6b48e586, 468dd559|registry = existing _REMOTE_SECRET_VALUES list; decrypt raises NoRedactionScopeError outside a scope (startup/liveness decryptions must open redaction_scope); RedactionScopeMiddleware outermost; service_sync_run split; tests written after code, revert check: apply_guard.py:303 wiring has no failing test; secret-redaction.md not updated; T030 must call register_secret_values in flow context; T036 run free-text checks before decrypt
6|T021,T026,T027,T029|4/0/0|512efb76, 1f7a57d4|receipt check renamed + start-up swap of earlier check (A1); HMAC request fingerprint from derived key; key unset -> 503 credential-key-not-configured on changes; event types infrahub_sync.credential.*; details.revision; created_by/updated_by in ACCOUNT_FIELDS; store/receipt redaction uses process secrets only (LiveSecretValues.fixed)
7|T022,T023,T028,T030|4/0/0|6c048fc0, a4e5649f|names read from stored version declared_content; in-progress = claimed, no terminal_at, matching flow_run_id; only needed fields released; event details.revision only when 1 credential (per-block revisions in run_credential_uses + log); extra codes credential-method-unknown, configuration-version-unavailable; client reason release-refused (T033 must map); client trust_env=False, SSL_CERT_FILE trust, no redirects; UNTESTED: credential-method-unknown, configuration-version-unavailable, block needing no credential, run without config_id
8|T024,T025,T031-T036|7/1/0|35d130ab, d8a5000a, 54718f5d|T032 partial: schedule branch-review read of earlier proposed changes runs before the run exists -> A2, chunk 8b. New failure type credential_unavailable (FAILURE_TYPES=25); token to schema read via context variable; run start checks only the stage's blocks; HTTP registration refusal = configs-validation + reason, findings from validate route; new module configuration/credential_admission.py; scheduled start: no run-start check, readiness read without token; NOT test-first: T031, T032, part of T036 (test_runtime_released_values, test_stage_stored_credentials, 8 cases of test_stored_destination_token); redaction test never failed; NO E4 concurrency test (give to deletion chunk 17); commits carry Fable trailer, not Opus (not amended)
8b|T032 (rest)|1/0/0|f2e0c9fb, 340bcd96|A2 option (c): POST /api/worker/schedules/{schedule_id}/proposed-change-states, Sync API decrypts and reads Infrahub on main, returns states only; 424 proposed-change-read-failed with details.failure; 422 destination-credential-not-named; no audit event, log line credential.used_for_proposed_change_read; fail closed: new cause proposed-change-states-incomplete; WORKER_ROUTES pinned in test_worker_route_boundary.py; LIMIT: Infrahub SDK waits 60 s per read, worker client 10 s x 3 retries -> slow Infrahub ends as credential_unavailable with up to 4 overlapping reads; credential-not-used reason on POST /configs not route-tested; boundary pin and 2 other tests written after code; full unit tier not rerun after 340bcd96 (5 files rerun)
9|T037,T038|2/0/0|48f26f1c, adabf705|prompt asks once (R10); --json on list/show only; set prints credential+revision; invalid name exit 2 before prompt; fixed refusal texts keyed on status+code; changed 3 existing CLI tests (secret-option rule allows --token-stdin, parity rows, import test checks cryptography); repository-tour.md + cli.mdx regenerated; AGENTS.md command list left for T059; secret-parameter-refused has no fixed CLI text; flaky: tests/test_benchmark_runner.py 2 failures in first unit run, passed alone and in final run; failing-first reason not captured
10|T039-T041|3/0/0|a5e31a47, c7c90a6f, 87ea2c04, ddf3bd90|new builders credential_package_data()/credential_package() in tests/configuration/validation_packages.py (package_data() stays old format for ~40 files of T042/T043/T055); also changed 3 example config.yml, tasks/bench.py v2_inputs, test_schedules.py, test_stored_credentials.py; example names netbox-token, infrahub-token, slurpit-api-key, apic-account (quickstart fixed by orchestrator); create_stored_credential_names() helper (no service extra); test_validate_destination_schema.py publish_worker_environment line -> T051; 2 tests changed coverage (bundled setting shapes declare no credential; checksum test reorders configuration keys); one failed intermediate run sent a GET to localhost:8000 (other session's stack) -> 401, fixed before commit; until T044 the shipped NetBox package is refused on dev stack/bench/nightly/Compose with credential-not-found; old-format test list for Phase 5 saved in scratchpad/phase5-old-format-tests.md
11|T042,T043|2/0/0|bac156d4, 7a87908c, a1e5bb4d|product fix: _worker_execution_context raises UnsupportedSyncStoreError before asking for stored credentials (test_sync_store_policy would otherwise weaken); NOT KNOWN whether the release route refuses a version with a store block; new helper serve_released_credentials() (real client over MockTransport); directly-called stage functions run in redaction_scope(); fake states route in test_schedule_branch_review; _named_source_package names no source credential (non-bundled adapter -> ConfigsInternalError); interrupted by 5-hour usage limit and resumed; port 8000/8001 guard plugin recorded no hit; canary tests also pass with a release fake that registers nothing
12|T044-T046|2/1/0|23c62c7b, 3cb0d075, a76e866e|T044 partial: dev.start/dev.infrahub/preview/demo and bench store infrahub-token and netbox-token via SyncClient as admin (create, set on credential-exists); _stack_env + 2 constants stay in tasks/dev.py (value source; no later task lists dev.py); dev.deps stores nothing; until T054 adds INFRAHUB_SYNC_ENCRYPTION_KEY the API answers credential-key-not-configured and dev.start stores nothing; nightly: comments + one constant. Names: one setting -> <adapter>-<setting>, username+password -> <adapter>-account; shipped apic-account and slurpit-api-key are kept because conversion keeps an existing credential: key (orchestrator decision: no rename). New module conversion_credentials.py; COMPOSE_WORKER_CREDENTIAL_VARIABLES built in credentials.py, re-assigned in conversion.py (T053 removes both); report layout changed ahead of T071; configs convert help text stale (T071); smoke suite moved (no task listed it). Old format remains in Compose/testcontainers (T054), tests/compose (T055), docs (T059/T060/T072), README.md and 4 tests/integration files (NO OWNER TASK), tests/dev_stack/test_compose_valid.py and test_deps_mode.py. Not run: dev.start, dev.smoke, bench.run-cell, nightly_e2e.py
13 | T047-T053 | 6 done, T049 partial (env loop of select_runtime_credential kept) | 9b251805 bf0185f5 22eb0d61 d5194d82 | refuses every stored version (all hold credentials: {}); refusals added on approval, states route, scheduled start, worker flow, registration, validation; worker_environments table kept unused; A3 follow-up 13b
13b | A3 follow-up | refusal narrowed for stored versions (empty block read, stored checksum kept); read routes 200; schedule routes 422; T049 stays partial (direct Prefect flow needs env reads; spec.md:334 keeps that flow) | fa957165 2412a942 ca183709 f189abdd | re-registering the same file for an empty-block version creates a new version (checksum differs); .venv briefly switched to 3.10 and restored
14 | T054-T056 | 3 done (T055 compose/docker/smoke/integration cases changed, collection only) | 7a3cc0da 5a78af98 78f96e8f | operator compose requires key and worker token (:?); dev compose fixed dev defaults; dev task overrides renamed INFRAHUB_SYNC_DEV_*_TOKEN; development-stack.mdx still names old exports (docs chunk); tests/image/conftest.py starts API without a key (not in scope)
15 | T057-T062 | 6 done | 030c2a1b 13344215 63e061f1 a710929d 6c48c757 ddfc6bea f651457d 783201dc ee7ca279 | ADR 0057 (0039 superseded, 0026/0040 partly); constitution 1.3.0; docs written from code where code differs from spec (lenient start, A4); unit tier 1 failure (marker test, order-dependent); changelog fragments to delete/reword at release listed
15b | A4 follow-up | Sync API process refuses to start without key or worker token (503 kept inside build_app); worker unchanged (run fails naming both variables; docs say so); marker test fixed (searches Prefect's resolved home and database) | ab4fb381 19f6507c 5c7d919a 11f26774 | lenient start came from chunks 3 and 6 ("until T054"); dev.api now uses build_process_service; tests/image gets throwaway key; worker token minimum is 16 characters per contract and code
16 | T063-T065 | 3 done | 964f36a1 50cdf84e a1d38916 | credentials_used on every run answer (contract names only GET /runs/{id}); approve answer fixed to carry it; runs show line format chosen by the chunk; frontend types.generated.ts not regenerated (codegen reads a live API on forbidden port 8001)
17 | T066-T069 + E4 test | 4 done; E4 registration race done (row lock on PostgreSQL READ COMMITTED, write lock on SQLite); E4 run-start race partial (no lock: run accepted, then refused at release with 422, no value released) | ef836f11 b93569be 5b763472 67055ce4 7eb95b58 d117ef7d | schedule create/re-pin takes no lock (R8 leaves it to run start); new service/credential_models.py (Pylint C0302); 204 receipt stores empty body
18 | T070-T072 | 3 done | 57b2e978 21a1e160 9de5a8ca 5749ffd3 | report already written by chunk 12; help text fixed; 422 configs-validation answer gains details.credentials [{block,name,code,fields}] (contract updated); finding messages name fields and --username; documented refusal output produced by a scratch script, CLI tests use a canned 422
19 | T073-T074 | T073 done; T074 prototypes built, waits for maintainer choice (notes.md); T075-T078 not started | fe8f1817 a8bd85e0 6e06156a | types generated from in-process app.openapi() (no port 8000/8001); frontend/vendor/infrahub submodule initialised; prototype route /proto/credentials must be removed before merge; spec 025 not found, no drop-area offer
20 | T081 | partial: format, lint, ruff, ty (exit 0, 31 warnings), CLI sanity, unit tier pass; quickstart scenarios 3-7 not run (development stack belongs to another session); T081 left unticked | none | -
```

## Local-pass evidence

One line per run: chunk, tests, command, time (UTC), environment, result line. The lines marked
`NOT RUN` list the tiers that need Docker images or the development stack.

```text
tests/service/test_credential_settings.py (43)|unit|uv run pytest tests/service/test_credential_settings.py -q|2026-11-20T16:16:50Z|n/a|43 passed in 0.13s
tests/service/test_credential_crypto.py (12)|unit|uv run pytest -q tests/service/test_credential_crypto.py|2026-11-20T16:41:59Z|n/a|12 passed in 0.14s
tests/product_store/test_stored_credentials.py[postgresql] (31)|integration|uv run pytest -q -m integration -k postgresql tests/product_store/test_stored_credentials.py|2026-11-20T16:41:31Z|PRODUCT_STORE_TEST_POSTGRESQL_DSN=sync027_store|31 passed, 62 deselected in 11.77s
tests/product_store/test_stored_credentials.py all profiles (93)|unit|uv run pytest -q tests/product_store/test_stored_credentials.py|2026-11-20T16:41:44Z|DSN set|93 passed in 13.03s
tests/product_store/test_contract.py (modified emulator)|unit|uv run pytest -q tests/product_store/test_contract.py|time not recorded (in unit tier 16:35-16:41Z)|n/a|460 passed, 42 skipped
tests/service/test_worker_account.py + test_worker_route_boundary.py (93)|unit+integration|PRODUCT_STORE_TEST_POSTGRESQL_DSN=... uv run pytest -p no:cacheprovider -m "" -v tests/service/test_worker_account.py tests/service/test_worker_route_boundary.py|2026-11-20T17:02:17Z|PRODUCT_STORE_TEST_POSTGRESQL_DSN=sync027_store|93 passed, 1 warning in 11.28s
tests/product_store/test_contract.py::test_public_surface_is_exactly_the_supported_contract|unit|uv run pytest tests/product_store/test_contract.py::test_public_surface_is_exactly_the_supported_contract|2026-11-20T17:01Z|n/a|1 passed
tests/configuration/test_block_credential.py (59), test_credential_field_map.py (66), test_validation_core.py (2 changed), test_parse_diagnostics.py named-credential (10)|unit|uv run pytest tests/configuration/test_block_credential.py tests/configuration/test_credential_field_map.py tests/configuration/test_validation_core.py::... tests/configuration/test_parse_diagnostics.py -v|2026-11-20T17:41:25Z|n/a|137 passed, 225 deselected in 0.22s (-k subset); each PASSED in -v
tests/service/test_released_value_redaction.py (15), test_stored_credentials_helper.py (5), test_credential_crypto.py::test_a_value_decrypts_to_the_fields_it_was_encrypted_from (modified)|unit|uv run pytest tests/service/test_released_value_redaction.py tests/service/test_stored_credentials_helper.py tests/service/test_credential_crypto.py::test_a_value_decrypts_to_the_fields_it_was_encrypted_from -v|2026-11-20T18:03:17Z|SQLite, Prefect test harness|21 passed, 1 warning in 6.70s
tests/service/test_credential_routes.py (59) + test_account_presentation.py + test_http_api.py + test_worker_route_boundary.py|unit|uv run pytest tests/service/test_credential_routes.py tests/service/test_account_presentation.py tests/service/test_http_api.py tests/service/test_worker_route_boundary.py -q|2026-11-20T18:50:02Z|n/a|309 passed, 1 warning
tests/client/test_credential_methods.py (14)|unit|uv run pytest tests/client/test_credential_methods.py -q|2026-11-20T18:31Z|n/a|14 passed
tests/product_store/test_stored_credentials.py receipt cases + test_contract.py|integration|uv run pytest tests/product_store/test_contract.py tests/product_store/test_stored_credentials.py -q -m "integration or not integration"|2026-11-20T18:41:22Z|PRODUCT_STORE_TEST_POSTGRESQL_DSN=sync027_store|600 passed, 1 warning
tests/client/test_models.py, test_schedule_routes.py, test_serve_site.py (modified)|unit|uv run invoke tests.tests-unit|2026-11-20T18:51:22Z|n/a|11203 passed
tests/service/test_credential_client.py (50) + test_worker_credentials_route.py (31) + test_parity_and_closure.py::test_only_shared_client_constructs_sync_api_http_requests + test_http_api.py::test_confirmation_schema_errors_and_openapi_contract|unit|uv run pytest tests/service/test_credential_client.py tests/service/test_worker_credentials_route.py tests/cli/test_parity_and_closure.py::test_only_shared_client_constructs_sync_api_http_requests tests/service/test_http_api.py::test_confirmation_schema_errors_and_openapi_contract -v|2026-11-20T19:44:08Z|SQLite tmp_path|81 passed, 1 warning in 5.21s
tests/product_store/test_credential_admission.py|unit+integration|uv run pytest tests/product_store/test_credential_admission.py -q -m 'not integration' (and -m integration with PRODUCT_STORE_TEST_POSTGRESQL_DSN)|2026-11-20T20:40:35Z|SQLite; PostgreSQL sync027_store|32 passed, 8 deselected / 8 passed, 32 deselected in 5.49s
tests/runtime_schema/test_stored_credential_execution.py|unit|uv run pytest tests/runtime_schema/test_stored_credential_execution.py -q|2026-11-20T20:41Z|n/a|4 passed, 1 warning in 14.37s
tests/service/test_credential_failure_cause.py|unit|uv run pytest tests/service/test_credential_failure_cause.py -q|2026-11-20T20:41Z|n/a|11 passed in 0.45s
tests/service/test_stored_destination_token.py|unit|uv run pytest tests/service/test_stored_destination_token.py -q|2026-11-20T20:41Z|n/a|12 passed, 1 warning in 1.33s
tests/service/test_stage_stored_credentials.py (written after code)|unit|uv run pytest tests/service/test_stage_stored_credentials.py -q|2026-11-20T20:41Z|n/a|14 passed in 0.66s
tests/configuration/test_runtime_released_values.py (written after code)|unit|uv run pytest tests/configuration/test_runtime_released_values.py -q|2026-11-20T20:41Z|n/a|9 passed in 0.14s
tests/service/test_worker_credentials_route.py (modified)|unit|uv run pytest tests/service/test_worker_credentials_route.py -q|2026-11-20T20:41Z|SQLite tmp_path|31 passed, 1 warning in 4.57s
tests/plan/test_report_models.py (modified)|unit|uv run pytest tests/plan/test_report_models.py -q|2026-11-20T20:41Z|n/a|14 passed in 0.02s
check-310 after chunk 8|unit 3.10|uv run invoke tests.check-310|2026-11-20T20:26:48Z|python 3.10|7702 passed, 119 skipped
unit tier at HEAD 54718f5d|unit|uv run invoke tests.tests-unit|2026-11-20T20:50:08Z|n/a|11364 passed, 4 skipped, 2 xfailed
tests/service/test_worker_proposed_change_states_route.py (new) + test_stage_stored_credentials.py + test_credential_client.py + test_worker_route_boundary.py + test_http_api.py (modified)|unit|uv run pytest <5 files> -v -p no:cacheprovider|2026-11-20T21:13Z|SQLite tmp store|333 passed, 1 warning in 22.82s
unit tier at f2e0c9fb|unit|uv run invoke tests.tests-unit|2026-11-20T21:0xZ|n/a|11437 passed, 4 skipped, 2 xfailed
tests/cli/test_credentials_command.py (new, 58) + 4 modified CLI tests|unit|uv run pytest tests/cli/test_credentials_command.py <4 node ids> -v -p no:cacheprovider|2026-11-20T21:43:32Z|fake Sync API|62 passed in 0.78s
unit tier after chunk 9|unit|uv run invoke tests.tests-unit|2026-11-20T21:43Z|n/a|11496 passed, 4 skipped, 2 xfailed
check-310 after chunk 9|unit 3.10|uv run invoke tests.check-310|2026-11-20T21:4xZ|python 3.10|8022 passed/111 skipped; base 7758 passed/119 skipped
15 moved files tests/configuration + tests/product_store (1434 node ids, identical before/after)|unit+integration|uv run pytest tests/<file> -q -p no:cacheprovider -o addopts="" -v|2026-11-20T22:19:51Z-22:39:41Z|PostgreSQL sync027_store for product_store|all same counts (e.g. test_contract.py 502 passed in 29.48s)
unit tier after chunk 10|unit|uv run invoke tests.tests-unit|2026-11-20T22:29:05Z|n/a|11496 passed, 4 skipped, 2 xfailed
check-310 after chunk 10|unit 3.10|uv run invoke tests.check-310|2026-11-20T22:3xZ|python 3.10|8024 passed/111 skipped; 7760 passed/119 skipped
T042/T043 moved files (tests/service, tests/runtime_schema, plan, aci, cli parity, canary; identical node ids via junit)|unit+integration|uv run pytest tests/service tests/runtime_schema tests/plan/test_report_credentials_absent.py tests/adapters/test_aci_verify_precedence.py tests/cli/test_parity_and_closure.py tests/test_credential_canary.py -q|2026-11-21T00:27:45Z|PostgreSQL DSNs of the brief, no INFRAHUB_SYNC_CREDENTIAL_*|3550 passed (before: 3550 passed in 275.13s)
unit tier after chunk 11|unit|uv run invoke tests.tests-unit|2026-11-21T00:35Z|n/a|11496 passed, 4 skipped, 2 xfailed
check-310 after chunk 11|unit 3.10|uv run invoke tests.check-310|2026-11-21T00:4xZ|python 3.10|8024 passed/111 skipped; 7760 passed/119 skipped
conversion tests (test_conversion*.py, test_convert_command.py, test_conversion_live_schema.py)|unit|uv run pytest <5 files> -q -p no:cacheprovider|2026-11-21T01:10:25Z|n/a|369 passed in 6.63s
tests/dev_stack/test_dev_tasks.py + test_benchmark_runner.py + test_nightly_e2e.py|unit|uv run pytest <3 files> -q -p no:cacheprovider|2026-11-21T01:10:25Z|n/a|316 passed in 16.81s
tests/dev_stack/smoke/test_smoke_request_shapes.py|unit|uv run pytest tests/dev_stack/smoke/test_smoke_request_shapes.py -v|2026-11-21T01:14:04Z|n/a|23 passed in 0.07s
tests/dev_stack/smoke/test_netbox_package.py, test_service_api.py|smoke (live)|uv run invoke dev.smoke|deferred — live development stack not run (owned by another session)|n/a|collected only
unit tier after chunk 12|unit|uv run invoke tests.tests-unit|2026-11-21T01:0xZ|n/a|11524 passed, 4 skipped, 2 xfailed
check-310 after chunk 12|unit 3.10|uv run invoke tests.check-310|2026-11-21T01:1xZ|python 3.10|7788 passed, 119 skipped
13 | unit tier | uv run invoke tests.tests-unit | 2026-11-21T02:25:26Z | n/a | 11453 passed, 4 skipped, 2 xfailed (tree of 22eb0d61)
13 | 21 changed test files | uv run pytest <files> -m "not integration and not smoke and not docker and not compose" | 2026-11-21T02:39:48Z | n/a | 1770 passed, 46 deselected (HEAD d5194d82)
13 | product store PostgreSQL | uv run pytest tests/product_store with 3 DSNs | 2026-11-21T02:28:46Z | sync027-pg 127.0.0.1:55427 | 1863 passed
13 | check-310 | uv run invoke tests.check-310 | 2026-11-21T02:34:14Z | Python 3.10 | 7998 passed / 7728 passed, all three legs
13 | not run | tests/integration (3 files), tests/compose docker tests | - | needs live stack; compose waits for T054/T055 | -
13b | 55 selected tests (removed format, approval, stage, schedule routes) | uv run pytest ... -k "..." | 2026-11-21T03:06:25Z | SQLite tmp_path | 55 passed, 122 deselected
13b | checksum pin | uv run pytest tests/configuration/test_removed_credential_format.py -k earlier_build_stored | 2026-11-21T03:09:34Z | n/a | 1 passed
13b | unit tier | uv run invoke tests.tests-unit | before f189abdd | n/a | 11489 passed, 4 skipped, 2 xfailed
13b | check-310 | uv run invoke tests.check-310 | before f189abdd | Python 3.10 | all three legs passed
14 | marker test + compose/dev_stack/testcontainers unit cases | uv run pytest -m "not integration and not smoke and not docker and not compose" tests/test_stored_credential_marker.py tests/compose tests/dev_stack tests/testcontainers tests/test_benchmark_runner.py tests/test_nightly_e2e.py | 2026-11-21T03:54:28Z | Docker CLI compose config only | 847 passed, 96 deselected
14 | unit tier | uv run invoke tests.tests-unit | chunk 14 | n/a | 11403 passed, 4 skipped, 2 xfailed
14 | check-310 | uv run invoke tests.check-310 | chunk 14 | Python 3.10 | 7932 / 7647 passed
14 | NOT RUN | uv run pytest -m compose tests/compose (64); -m docker tests/compose (6); -m integration tests/integration (70); uv run invoke dev.smoke (25) | deferred | needs image from this checkout and a live stack | -
15 | tutorial pin | uv run pytest tests/cli/test_parity_and_closure.py::test_netbox_tutorial_starts_and_authenticates_the_service_boundary -v | 2026-11-21T05:38:45Z | n/a | PASSED
15 | documentation tests | (unchanged files) | 2026-11-21T05:38:45Z | n/a | 227 passed
15 | unit tier | uv run invoke tests.tests-unit | 2026-11-21T05:40:49Z | no DSN | 1 failed (test_stored_credential_marker), 11407 passed -> chunk 15b
15b | serve required settings + 6 modified files | uv run pytest <7 files> | 2026-11-21T06:20:53Z | n/a | 146 passed, 13 skipped
15b | marker test alone | uv run pytest tests/test_stored_credential_marker.py | 2026-11-21T06:19:58Z | n/a | 1 passed
15b | unit tier at 11f26774 | uv run invoke tests.tests-unit | 2026-11-21T06:13:51Z | 3 PostgreSQL DSNs | 11428 passed, 4 skipped, 2 xfailed, 0 failed
15b | check-310 | uv run invoke tests.check-310 | chunk 15b | Python 3.10 | all three legs passed
15b | NOT RUN | uv run pytest tests/image -m docker (68 collected) | deferred | needs Docker and an image | -
16 | revisions, approve, runs show (10 tests) | uv run pytest tests/service/test_credential_revisions_on_runs.py tests/service/test_approval.py tests/cli/test_credentials_command.py -k "..." | 2026-11-21T06:45:41Z | SQLite | 10 passed
16 | unit tier at a1d38916 | uv run invoke tests.tests-unit | 2026-11-21T06:52:54Z | 3 PostgreSQL DSNs | 11438 passed, 4 skipped, 2 xfailed, 0 failed
16 | check-310 | uv run invoke tests.check-310 | before a1d38916 | Python 3.10 | all three legs passed
17 | product store incl. 36 deletion cases (12 PostgreSQL) | uv run pytest tests/product_store -m "integration or not integration" | 2026-11-21T07:31:23Z | 3 PostgreSQL DSNs | 1899 passed
17 | service/client/CLI deletion + parity + contract + worker boundary | uv run pytest <6 files> | 2026-11-21T07:32:10Z | n/a | 715 passed
17 | unit tier | uv run invoke tests.tests-unit | before d117ef7d | n/a | 11504 passed, 4 skipped, 2 xfailed, 0 failed
17 | check-310 | uv run invoke tests.check-310 | chunk 17 | Python 3.10 | 7699 passed, all three legs
18 | convert, admission, failure cause, conversion, credentials command | uv run pytest <5 files> -v | 2026-11-21T07:57:55Z | 3 PostgreSQL DSNs | 411 passed
18 | unit tier | uv run invoke tests.tests-unit | 2026-11-21T08:15:55Z | n/a | 11521 passed, 4 skipped, 2 xfailed, 0 failed
18 | check-310 | uv run invoke tests.check-310 | chunk 18 | Python 3.10 | 7713 passed, all three legs
19 | frontend checks | pnpm check; pnpm typecheck; pnpm build (in frontend/) | 2026-11-23T09:06:33Z | Node, pnpm | Checked 89 files, no fixes; tsc no error; built in 542ms
19 | prototype browser check | node scratchpad/c19/verify.cjs | 2026-11-23T09:06:19Z | headless Chromium, Vite on 127.0.0.1:5199, API answered in browser | 24 steps ok, no console or page error
20 | format, lint, ruff check ., ty check ., CLI --help (root, configs, runs, credentials) | see command names | 2026-11-23T09:07:27Z | HEAD 491979a4 | all exit 0; ruff "All checks passed!"; ty 31 diagnostics, exit 0
20 | unit tier at 491979a4 | uv run invoke tests.tests-unit | 2026-11-23T09:15:08Z | 3 PostgreSQL DSNs | 11521 passed, 4 skipped, 673 deselected, 2 xfailed
20 | NOT RUN | quickstart.md scenarios 3-7 (uv run invoke dev.start, then the scenario commands) | deferred | development stack owned by another session | -
```
