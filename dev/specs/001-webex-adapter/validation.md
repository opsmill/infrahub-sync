# Validation: Webex source adapter

## Scope and criteria

The criteria are the engineer's gap-closing task and `spec.md` from `a5eb00bc`, which remains byte-for-byte unchanged. Changes are confined to the Webex adapter, tests and invented fixtures, the single example, documentation, its changelog fragment and the requested review artifacts. Existing adapters, engine, CLI, dependencies and lockfiles are unchanged. No live Webex or Infrahub requests, syncs, pushes or PR creation are authorized for this run.

The engineer explicitly deferred persisted destination deletion after inspection showed that `InfrahubModel` implements create and update but no delete. The test proves exactly one device is marked for deletion, all other models are unchanged and the SDK fixture node remains. This is an infrahub-sync core limitation, documented in the adapter page and PR description; the US3 independent test remains partly met. The FR-004 renewal behavior and FR-012 OAuth wording remain unchanged for the engineer's decision.

## Fixture evidence

| Criterion | Evidence | Result |
| --- | --- | --- |
| 25 devices create in an empty destination | `test_25_devices_create_in_empty_destination_and_converge[True]` checks exactly 25 creates and every mapped device attribute, with a devices-only configuration. | PASS |
| Complete example with peers and stable identities | The same test with `[False]` uses the shipped three-kind project; `test_devices_reference_workspaces_created_after_locations` checks source peer identities, destination SDK peer IDs and location/workspace/device write order. | PASS |
| Unchanged second run converges | The create test repeats diff after sync and again after a fresh destination load from persisted fixture nodes. | PASS |
| One removed device changes no other node | `test_removing_one_device_marks_only_its_destination_node_for_deletion` checks one delete, zero creates/updates and unchanged other devices, locations and workspaces; persisted deletion is explicitly deferred. | PASS for diff; core limitation for persistence |
| Failed refresh preserves data-resource context; no credential output | `test_failure_matrix_names_source_resource_and_hides_credentials` asserts Webex, `/v1/locations` and credential-free logs and locals-enabled tracebacks for ten authentication, permission, network, rate-limit, server and refresh failure types. | PASS |
| Missing-key diagnostics name endpoint and key | `test_missing_response_key_names_endpoint_and_key` covers ordinary and dotted keys. | PASS |
| HTTP 400 repeats safe Webex text | Tests cover safe text, known/current/earlier/rotated credentials, special characters and URL encoding, bearer values, credential assignments and URLs; other response fields are excluded. | PASS |
| Start parameters still permit complete extraction | `test_start_cursor_still_loads_all_pages` covers before/after in params and endpoint queries; the first request normalizes away start cursors and follows returned continuation links. | PASS |
| One organization per project | Conflicting source/resource IDs fail before HTTP; endpoints and pagination cannot change scope; `test_two_projects_isolate_two_organizations` checks independent diffs and peers for two organization fixtures. | PASS |
| Several pages plus rate limit return every record | `test_multi_page_rate_limit_loads_every_record` loads 1,001 distinct records over three pages, honors Retry-After 7 and retries the identical middle-page request. | PASS |
| Fixed Calling workspace query | `test_calling_workspace_fixed_query_uses_returned_set` asserts type=meetingRoom on both requests and loads only the returned matching rooms. | PASS |
| Parent request counters include failures and retries | `test_many_parents_count_retries_and_failed_requests` exercises 500 parents, 100 HTTP 404s and one 429 retry: 400 records and exactly 501 counted/logged requests. | PASS |

## Checks

| Command | Observed output and exit | Result |
| --- | --- | --- |
| `uv sync --extra dev` | `Resolved 94 packages`; `Checked 85 packages`; exit 0; no dependency/lockfile changes. | PASS |
| `uv run invoke format` | `78 files left unchanged`; `All checks passed!`; exit 0. The formatter's two changes to the upstream spec were discarded to preserve its exact bytes. | PASS for allowed changes |
| `uv run invoke lint` | Exit 1 at rumdl: only `spec.md:114` and `spec.md:115` report baseline MD007 indentation. This immutable upstream file is not changed. | Baseline failure |
| `uv run invoke linter.lint-ruff` | `78 files already formatted`; exit 0. | PASS |
| `uv run invoke linter.lint-yaml` | No diagnostics; exit 0. | PASS |
| Explicit yamllint on the example config/schema | Default YAML rules with line length 120; no diagnostics, exit 0. The example schema also equals the invented destination fixture and validates all three NodeSchemaAPI kinds. | PASS |
| `uv run ty check .` | No errors; three existing unused-ignore warnings in unrelated optional-adapter tests; exit 0. | PASS |
| `uv run pylint infrahub_sync/adapters/webex.py infrahub_sync/adapters/webex_client.py tests/adapters/test_webex.py tests/adapters/test_webex_client.py tests/adapters/test_webex_examples.py --output-format=json` | `[]`; zero diagnostics; exit 0. | PASS |
| `uv run invoke linter.lint-pylint` | Exit 30; 56 existing diagnostics in unchanged code; score `9.60/10`, unchanged from baseline. | Baseline failure |
| `uv run pytest -q` | `301 passed, 3 skipped, 48 warnings in 10.28s`; exit 0. | PASS |
| `uv run infrahub-sync --help` | CLI help rendered; exit 0. | PASS |
| `uv run infrahub-sync list --directory examples/` | Exactly one Webex project: `webex-workspaces-to-infrahub` with source `webex` and destination `infrahub`; exit 0. | PASS |
| Fixture CLI generation | `test_cli_generate_uses_fixture_destination_schema` runs `generate` against a mocked destination schema; included in passing pytest suite. No live generation command is run. | PASS |
| `uv run invoke docs.generate` | Exit 0; the unrelated generated CLI-reference change was discarded. | PASS |
| `npx --yes --package=pnpm@11.6.0 --call 'uv run invoke docs.docusaurus'` | `[SUCCESS] Generated static files in "build".`; exit 0. Uses the package-manager version in docs/package.json because global pnpm is unavailable; no repository dependency changes. | PASS |
| Changed Markdown/MDX `rumdl check` | No issues in the four updated adapter/review Markdown/MDX files; exit 0. The changelog fragment is excluded by repository configuration and was reviewed directly. | PASS |
| `git diff --check` | No output; exit 0. | PASS |

The docs build reports existing Docusaurus deprecation, stale Browserslist data and Node localStorage warnings. Pytest warnings include generated-model local_id shadowing, Click deprecations and the existing unregistered timeout marker. No suppressions, type overrides or unrelated core cleanup were added to hide baseline findings.

The complete checks were run after implementation. The last record-only update changes this validation text and the matching audit/PR evidence; Markdown and whitespace checks were repeated on those artifacts.

## Documentation and scope review

This is an update to the existing adapter page at the same URL, under the existing Adapters sidebar entry; no sidebar or redirect changes are needed. The outline and prose check notes are local artifacts at `/tmp/webex-docs-outline.md` and `/tmp/webex-docs.checks.md`. The prose check records 48 sentence/subject/verb entries and 36 non-technical verb/expression entries; the page was also read continuously for naturalness and tables were checked against settings, mappings and error code. Configuration and behavior claims were checked against the two Webex modules and the example; persisted deletion was checked against the unchanged InfrahubModel. The added device read scope was checked against the [Webex scope reference](https://developer.webex.com/admin/docs/integration-scopes).

The docs skill's staged questions, branch creation, push and PR-opening steps were not used: the dispatched task already specifies the page, scope, unchanged spec, current branch and exactly one additional local commit, and explicitly forbids push/PR creation. The existing #396 PR-description sections are preserved. No GraphQL/API-interface tabs or screenshots apply to this CLI adapter-page update. The literal-language, causality, terminology, factual, correction, customer-data and cross-reference checks were applied to the edited file; all fixtures and examples use invented values.

Rules read: `gate-discipline.md`, `step-0-triage.md`, `step-0.5-detect-repo.md`, `step-1-preflight.md`, `step-2-audit.md`, `step-2.7-content-audit.md`, `domain-lifecycles.md`, `no-customer-pii.md`, `corrections-ledger.md`, `step-3-outline.md`, `docs-navigation-map.md`, `product-terminology.md`, `step-4-create-files.md`, `voice-and-tone.md`, `actors-and-causality.md`, `right-altitude.md`, `explain-and-describe.md`, `steps-vs-sections.md`, `failure-modes.md`, `checking-prose.md`, `step-6-build-verify.md`, `step-8-cross-reference.md`, `audit-rigor.md`, `spec-drift.md`, `step-9-pre-pr-audit.md`, `step-10-pr-description.md`, `step-11-lint-commit-push.md` and `_checklist.md`. Structural-pattern and tutorial files are not applicable because no new page or reorganization is introduced.

## Remaining specification gaps

| Row | Status | What remains |
| --- | --- | --- |
| FR-012 | Partly met | Literal read-only wording versus the existing OAuth refresh POST; engineer decision, behavior unchanged. |
| SC-001 | Partly met | Timed operator configuration exercise. |
| SC-002 | Partly met | Compare loaded device count with a real organization containing at least 1,000 devices. |
| SC-006 | Not met | Live example list/diff against Webex and Infrahub. |
| Multi-page edge case | Partly met | Live Webex count versus diff; mocked HTTP pagination/retry/count exercise passes. |
| US3 independent test | Partly met | Persisted Infrahub deletion; exact one-device delete diff passes, but core has no delete implementation. |

The complete updated table is in [audit.md](audit.md). All other previously partly met or not met fixture/implementation rows are now met. FR-004 retains its previous fixture-supported status and behavior while the engineer considers it.
