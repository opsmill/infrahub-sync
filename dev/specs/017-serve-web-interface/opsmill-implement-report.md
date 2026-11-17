# Implementation report: open the web interface from the address of the Sync API

- **Feature**: issue [#421](https://github.com/opsmill/infrahub-sync/issues/421), spec directory
  `dev/specs/017-serve-web-interface/`
- **Branch**: `017-serve-web-interface`
- **Base commit**: `daa45d28` (spec, plan, tasks and clarifications)
- **Head commit before this report**: `fcd44481`
- **Result**: DONE. 37 of 37 tasks complete; local-pass evidence for every test added or changed.
- **Wall-clock time**: about 3 hours 10 minutes (13:20 to 16:30 UTC on 2026-11-16).

## 1. Chunk ledger

| # | Chunk | Tasks | Outcome | Commits |
|---|---|---|---|---|
| 1 | Phase 1 and Phase 2: baseline, the outer application, the API under `/api` | T001-T008 (8) | 8 done | `03cece66`, `20414023` |
| 2 | Phase 3 (part): image `web` stage, `.dockerignore`, image tests | T009-T011 (3) plus one added step | 3 done | `b76650e8`, `b3416b38` |
| 3 | Phase 4: token entry and token handling in the web interface | T013-T021 (9) | 9 done | `96959857` |
| 4 | Phase 3 (end): image build and image tests | T012 (1) | 1 done | `fc59e028` |
| 5 | Phase 5: the client and the CLI under `/api` | T022-T027 (6) | 6 done | `17ab14e2` |
| 6 | Phase 6: development build, development server, CI, frontend README | T028-T032 (5) | 5 done | `0a3d415c` |
| 7 | Phase 7 (part): documentation, ADR 0043, changelog | T033-T035 (3) | 3 done | `ae0c02a0` |
| 8 | Phase 7 (end): full checks and the quickstart against the development stack | T036-T037 (2) | 2 done | `cde19f47`, `e2c5a5bb` |
| Review | Fixes from the review | 14 fixes | 14 done | `625fb6b6`, `c95c9fef`, `1d33c975`, `fcd44481` |

Points flagged by the chunks:

- Chunk 1: the baseline unit tier had 8517 passed, 4 skipped, 2 xfailed. `uv run invoke format`
  also reformatted 351 files inside the fetched `frontend/vendor/infrahub` submodule (nothing under
  `frontend/packages/ui`). Nothing was staged; the files were restored with the maintainer's
  approval, keeping the two `.spec-context.json` changes that existed before this run.
- Chunk 2: added a step outside `tasks.md`: `frontend/vendor` is excluded from Ruff, rumdl and ty
  (`b76650e8`), so the formatters no longer touch the submodule. yamllint already ignored it. The
  Dockerfile copies `fronten[d]/vendor/infrahub/frontend/packages/ui`: a plain `COPY` fails with
  "not found" before the check that names the submodule can run.
- Chunk 3: the "Administrator rights" row of the status menu stays (it names no user). The
  artifact download frees its temporary blob address after 60 seconds.
- Chunk 4: the three OCI label tests pass only when the build sets the labels that CI sets
  (`ci-docker-image.yml`), as the test file states.
- Chunk 5: `docs/docs/reference/python-api.mdx` is written by hand, so it was edited by hand.
  The not-a-sync-api check is one helper, `_require_json`, used by `_success`,
  `_raise_response_error`, `get_version` and `get_status`.
- Chunk 6: `test_only_the_newest_stable_release_is_called_newest` failed in 2 of 5 runs of its
  file. Chunk 8 ran it 10 times on this branch and 10 times on the base commit: all passed.
- Chunk 8: the browser check found that `style-src 'self'` blocks an inline `<style>` element that
  React Aria inserts at run time. The fallback of research R-3a was applied:
  `style-src 'self' 'unsafe-inline'`. Scripts stay limited to the service and the hashed inline
  script.

## 2. Tasks not completed

None. Every task in `tasks.md` is marked `[X]`.

## 3. Image

| Image | Size in bytes |
|---|---|
| `feature/v3-develop` at `e8f05899` | 1009087156 |
| this branch at `96959857` | 1010581128 |
| Difference | +1493972 (about 1.49 MB; SC-007 allows less than 10 MB) |

`/opt/infrahub-sync/web` holds `index.html` and `assets/` (1112 KB). `command -v node` in the image
prints `absent`.

## 4. Local-pass evidence

All runs on macOS, Python 3.11, the full development profile (`dev`, `prefect`, `service` extras),
2026-11-16. Docker is OrbStack.

| Test id | Type | Run command | Passed at (UTC) | Environment context | Verbatim pass line |
|---|---|---|---|---|---|
| `tests/service/test_settings_web_directory.py` (7) | unit | `uv run pytest <files of chunk 1> -q` | 2026-11-16T14:08:19Z | n/a | `269 passed, 1 warning in 6.59s` |
| `tests/service/test_environment_names.py` (modified) | unit | same run, then the review run | 2026-11-16T16:24:57Z | n/a | `269 passed, 1 warning in 3.36s` |
| `tests/service/test_http_errors.py` | unit | same chunk 1 run | 2026-11-16T14:08:19Z | n/a | `269 passed, 1 warning in 6.59s` |
| `tests/service/test_web_site.py` (all rows, CSP, review additions) | unit | `uv run pytest tests/service/test_web_site.py tests/service/test_serve_site.py tests/service/test_http_errors.py tests/service/test_environment_names.py tests/client -q -p no:randomly` | 2026-11-16T16:24:57Z | n/a | `269 passed, 1 warning in 3.36s` |
| `tests/service/test_serve_site.py` (lifespan, web directory) | unit | same review run | 2026-11-16T16:24:57Z | n/a | `269 passed, 1 warning in 3.36s` |
| `tests/dev_stack/test_deps_mode.py` (modified) | unit | chunk 1 run | 2026-11-16T14:08:19Z | n/a | `269 passed, 1 warning in 6.59s` |
| `tests/compose/test_compose_contract.py` (health checks) | unit | chunk 1 run | 2026-11-16T14:08:19Z | n/a | `269 passed, 1 warning in 6.59s` |
| `tests/image/test_build_context.py`, `test_version_policy.py` | unit | `uv run pytest -p no:cacheprovider -o addopts="" -q tests/image/test_build_context.py tests/image/test_version_policy.py` | 2026-11-16T14:20:43Z | n/a | `23 passed in 3.19s` |
| `tests/image/test_image_artifact.py` (35, including the web interface checks) | integration | `INFRAHUB_SYNC_DOCKER_IMAGE=infrahub-sync VERSION=web-check uv run --no-sync pytest -m docker tests/image/test_image_artifact.py` | 2026-11-16T14:56:24Z | image `infrahub-sync:web-check` built with the CI labels | `35 passed in 23.55s` |
| `tests/image` (all 64) | integration | `INFRAHUB_SYNC_DOCKER_IMAGE=infrahub-sync VERSION=web-check uv run --no-sync pytest tests/image -rs` | 2026-11-16T14:56:54Z | same image | `64 passed in 31.16s` |
| `tests/client/test_client.py` (address rule, review rows) | unit | review run above | 2026-11-16T16:24:57Z | n/a | `269 passed, 1 warning in 3.36s` |
| `tests/client/test_not_sync_api.py` (7 plus review additions) | unit | review run above | 2026-11-16T16:24:57Z | n/a | `269 passed, 1 warning in 3.36s` |
| `tests/client/test_public_surface.py`, `test_resume.py`, `test_wait.py` | unit | `uv run pytest -q -p no:cacheprovider <file>` | 2026-11-16T15:13:48Z | n/a | `9 passed`, `5 passed`, `15 passed` |
| `tests/cli/test_client_commands.py`, `test_apply_confirm_left_out.py` | unit | same | 2026-11-16T15:13:48Z | n/a | `195 passed in 2.15s`, `7 passed in 0.27s` |
| `tests/service/test_prefect_wording.py` | unit | same | 2026-11-16T15:13:48Z | n/a | `5 passed, 1 warning in 10.62s` |
| `tests/dev_stack/test_build_inputs.py`, `test_dev_tasks.py`, `tests/test_workflow_contracts.py` | unit | `uv run pytest tests/dev_stack/test_dev_tasks.py tests/dev_stack/test_build_inputs.py tests/test_workflow_contracts.py -q` | 2026-11-16T15:22:25Z | n/a | `221 passed in 21.15s` (workflow contracts, rerun); build inputs 21 passed |
| Unit tier, final | unit | `uv run invoke tests.tests-unit` | 2026-11-16T16:30:07Z | n/a | `8630 passed, 4 skipped, 374 deselected, 2 xfailed, 15 warnings in 299.98s (0:04:59)` |
| Live tests changed in T026 (`tests/dev_stack/smoke`, `tests/compose`, `tests/integration`) | e2e | `uv run pytest --collect-only -q tests/dev_stack/smoke tests/compose tests/integration` | deferred — needs the development stack, Compose and NetBox; collection only: `594 tests collected in 2.55s` | n/a | n/a |
| Frontend checks | build | `cd frontend && pnpm check && pnpm typecheck && pnpm build` | 2026-11-16T15:19:22Z | Node from Corepack | `Checked 74 files in 38ms. No fixes applied.`; `tsc --noEmit` exit 0; `✓ built in 444ms` |

### Quickstart scenarios 3 and 4 (T037)

Run against `uv run invoke dev.deps` and
`INFRAHUB_SYNC_WEB_DIRECTORY=<repository>/frontend/dist uv run invoke dev.api`, on 2026-11-16.

| Request | Result |
|---|---|
| `GET /api/version` | 200, version JSON |
| `GET /api/runs` without a token | 401 |
| `GET /version` | 404, `{"error":{"code":"not-found","message":"the Sync API is served under /api",...}}` |
| `GET /runs/abc`, `Accept: text/html` | 200, byte-identical to `dist/index.html` |
| `GET /assets/missing.js` | 404 |
| `GET /api/openapi.json`, `.servers` | `[{"url":"/api"}]` |
| Headers on `/` | `cache-control: no-cache`, `x-content-type-options: nosniff`, `x-frame-options: DENY`, `referrer-policy: no-referrer`, the Content-Security-Policy, no `set-cookie` |

CLI:

- `infrahub-sync whoami`, `configs list` and `runs list` give the same result with
  `INFRAHUB_SYNC_API_URL=http://127.0.0.1:8001`, `.../api` and `.../api/`. The service log shows
  only `/api/...` requests and no `/api/api`.
- Against the Vite development server, `http://localhost:3000` works, because Vite forwards `/api`.
  `http://localhost:3000/sync` answers HTML and prints `error: not-a-sync-api` with
  `operation: get_version`, `address: http://localhost:3000/sync/api/version`, `status: 200` and
  the hint. Exit code 1.

Browser (headless Chrome 148 driven through the DevTools protocol, one context per step):

- `/` displays "Enter a Sync API token".
- A wrong token displays "The Sync API refused the token."
- A valid token displays the configurations page. `sessionStorage` holds
  `infrahub-sync.api-token`; `localStorage` and cookies are empty.
- A new context opening `/runs/00000000-0000-0000-0000-000000000000` displays the token entry
  screen, then the run page ("No run with this ID") at the same address after the token.
- "Remove token" in the status menu returns to the token entry screen and empties
  `sessionStorage`.
- Console after the style fallback: no Content-Security-Policy message.

### Live tiers (run after the report, 2026-11-16)

Run as `docs/docs/development/guidelines/testing-tiers.md` describes. Every request to an `/api`
address worked. No failure comes from this branch, and no code changed.

| Tier | Command | Result | Failures |
|---|---|---|---|
| Smoke | `uv run invoke dev.start`, `uv run invoke dev.smoke` | `3 failed, 20 passed, 33 deselected in 210.80s` | Earlier problem: two tests in `tests/dev_stack/smoke/test_config_lifecycle.py` and `test_netbox_package.py::test_validating_the_netbox_package_reads_no_source` expect `declared_content` in registration answers, which spec 007 FR-038 removed |
| Smoke, unmarked modules | `uv run pytest -q -m "not smoke" -rs tests/dev_stack/smoke` | `32 passed, 1 skipped, 23 deselected` | Skipped: the resume check needs the NetBox `demo` package (optional) |
| Compose | `INFRAHUB_SYNC_DOCKER_IMAGE=infrahub-sync VERSION=compose-test uv run pytest -m compose tests/compose -rs` | `1 failed, 63 passed, 407 deselected in 1017.84s` | Earlier problem: `test_managed_run.py::test_the_deployed_worker_container_mounts_nothing` does not know the read-only `sync-ca-certificates` volume added by spec 015 |
| Integration, step 3 | `uv run pytest -m integration -rs` | `1 failed, 231 passed, 12 skipped in 303.27s` | Unstable: `test_apply_guard_integration.py::test_a_body_exception_releases_the_real_lock` failed in 1 of 10 runs in random order and passed in 5 of 5 runs in fixed order. Skips: optional lineage, remote-run and resume legs; saved-plan tests run in step 4 |
| Integration, step 4 | `uv run pytest -m integration -rs tests/integration/test_saved_plan_apply_integration.py` | `6 passed, 1 skipped in 38.77s` | Skipped: the optional ambiguous-peer test (SC-016) |

The development stack and the local NetBox were destroyed afterwards. The image smoke tier ran
earlier (section 4). One stale line was found and corrected: `testing-tiers.md` described the
image check as calling `GET /version`.

## 5. Review findings

Six reviewers ran on `daa45d28..e2c5a5bb`: code, tests, error handling, comments, types, and
simplification. No finding was critical.

| Severity | File | Finding | Status |
|---|---|---|---|
| High | `infrahub_sync/service/web.py` | An `index.html` that cannot be read or decoded stopped the service at startup | Fixed in `625fb6b6` |
| Medium | `infrahub_sync/service/serve.py` | `build_service(**factories)` lost the keyword-only signature of `build_app` | Fixed in `625fb6b6` |
| Medium | `tests/service/` | No tests for a symbolic link that leaves the directory, unusual paths, `build_service` with a web directory, or the `http-error` code | Fixed in `625fb6b6` |
| Medium | `dev/specs/017-serve-web-interface/quickstart.md`, `research.md` | Wrong Vite example; R-3a showed the earlier style policy | Fixed in `1d33c975` |
| Medium | `infrahub_sync/client/client.py` | A proxy that answers 502, 503 or 504 with an HTML page gives `not-a-sync-api`, whose hint points at the address instead of the stopped service | Deferred: the contract requires this answer (T024 tests an HTML 502); needs a decision |
| Medium | `frontend/src/components/token-entry.tsx` | Any answer other than 200 or 401 (for example 403, 429, 500) displays "The Sync service did not answer" | Deferred: the contract names this text; changing it touches the colleague's code |
| Low | `infrahub_sync/service/web.py` | `//assets/missing.js` returned the entry page; `TRACE` got a plain 405 without the security headers | Fixed in `625fb6b6`, `fcd44481` |
| Low | `infrahub_sync/client/client.py` | Operation name hard-coded in the error-envelope check; duplicate JSON check in `get_status` | Fixed in `c95c9fef` |
| Low | `changelog/421.added.md`, ADR 0043, `docs/docs/development-stack.mdx` | "Not included" answer goes to browsers only; `GET /status` without `/api` | Fixed in `1d33c975` |
| Low | `frontend/src/api/token.ts` | When tab storage fails, the token is lost on reload without a notice | Deferred (edge case listed in the spec) |
| Low | `frontend/src/features/runs/run-sections.tsx` | The artifact download has no timeout | Deferred |
| Low | `tasks/dev.py`, `Dockerfile` | The list of frontend build inputs is written twice and can drift | Deferred |
| Advice | `frontend/src/features/runs/plan-download.tsx`, `run-sections.tsx` | The same fetch-with-token block appears twice; one helper would remove it | Deferred: `frontend/` belongs to another maintainer |
| Advice | `infrahub_sync/service/web.py` | `create_site` could take a type that guarantees a readable `index.html` | Deferred |

## 6. Autonomous decisions

- T001 ran in the first chunk instead of a chunk of its own, because it changes no code.
- T012 (image build) ran as its own chunk after the web interface chunk, as `tasks.md` orders.
- Added the exclusion of `frontend/vendor` from the lint and format gates (`b76650e8`). Without
  it, every `uv run invoke format` rewrites the submodule.
- Applied the Content-Security-Policy fallback `style-src 'self' 'unsafe-inline'` after the
  browser check, as research R-3a planned. ADR 0043 records it.
- The live tests changed in T026 were not run; they need the development stack, Compose and
  NetBox. They were checked by collection only.
- Two medium review findings were deferred because the contract specifies the current behaviour
  (see section 5).
- Prefect's address (port 4230) answers JSON at `/api/version`, so the CLI there prints
  `error: compatibility` with no server version, not `not-a-sync-api`. The contract does not cover
  this case.

## 7. Suggested next steps

1. Decide the two deferred contract points: the CLI answer for a proxy error page (5xx with HTML),
   and the token screen text for a 403, 429 or 500 answer.
2. Run the live tiers on the development stack and in Compose (`uv run invoke dev.smoke`, the
   Compose lifecycle tests) before a release.
3. Tell the owner of `frontend/` about the changes listed in the spec Assumptions, including the
   removal of the "Requests sent as" line.
4. Open a pull request into `feature/v3-develop` when ready.
