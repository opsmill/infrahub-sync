# Implementation Plan: Discoverable runs, configurations and versions, and consistent verbs

**Branch**: `015-run-discovery-api` | **Date**: 2026-11-04 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `dev/specs/015-run-discovery-api/spec.md`

## Summary

Add a paged, filterable run list (`GET /runs`), a token check (`GET /me`), named and paged
configuration lists, light version summaries with each version's latest run and latest
applied run, and identifying Prefect tags on every flow run Sync submits. Align the CLI with
the API verbs (`plan` replaces `diff`; add `verify`, `runs list`, `runs artifacts`,
`runs cancel`, `whoami`; accept a configuration name wherever an ID is expected). All paging
uses one keyset cursor model ordered newest first. Storage changes are additive and follow the
store's existing idempotent start-up migrations. Research: [research.md](research.md).

## Technical Context

**Language/Version**: Python 3.11–3.13 (service profile).

**Primary Dependencies**: FastAPI, Pydantic, Typer, HTTPX, Prefect 3.8.6 client (no new
dependency).

**Storage**: the product store (`_RelationalRunStore`) on SQLite (local and tests) and
PostgreSQL (service). Additive indexes and nullable columns; no backfill except
`configurations.name`.

**Testing**: pytest unit tier (`uv run invoke tests.tests-unit`): `tests/service/`,
`tests/product_store/`, `tests/client/`, `tests/cli/`. PostgreSQL isolation tests for the new
migration steps.

**Target Platform**: Linux server (Sync API and worker), CLI on any platform.

**Project Type**: web service with a CLI client.

**Performance Goals**: one page of 200 runs is served from one indexed query plus one query
for the page's version references; no per-row reads.

**Constraints**: API stays `v3-unstable`; vendored `opsmill_prefect_extras` stays
byte-identical; secrets never appear in responses, tags or logs.

**Scale/Scope**: designed for 10,000+ runs per configuration (SC-005).

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle (`docs/docs/development/constitution.md`) | Status |
| --- | --- |
| Non-applying commands stay safe; `sync`/`apply` need explicit approval | Pass. `plan`, `verify`, `runs *`, `whoami`, `configs *` reads do not write to a destination. `runs cancel` is an existing API mutation that already requires the run's actor or an administrator. The constitution line naming `diff` is updated to `plan` (wording only). |
| New mutating behavior behind explicit flags | Pass. No new destination mutation. The new `runs cancel` CLI command requires `--yes` or an interactive confirmation (critique E3). |
| Idempotency and safety | Pass. Migration steps are `IF NOT EXISTS` and catalog-checked; reads are idempotent. |
| Typed, Ruff/Pylint/ty clean, specific exceptions | Planned in every task. |
| Tests atomic and single-purpose | Planned: one behavior per test, parametrized edge cases. |
| No secrets in output | Pass. List items reuse the bounded emitted run resource; tags carry identifiers only; `/me` never returns the token. |
| New dependencies justified | Pass. None. |
| User-visible changes update `docs/` in the same change | Planned (Phase "Docs" tasks). |

**Governance gates crossed**: API change (new routes; breaking list response shape under
`v3-unstable`), auth surface (new caller-describing route), database schema change (indexes
and nullable columns). No new dependency, no CI change.

Post-design re-check: pass. No violations to justify.

## Project Structure

### Documentation (this feature)

```text
dev/specs/015-run-discovery-api/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── http-api.md
│   └── cli.md
├── checklists/requirements.md
└── tasks.md              # created by /speckit-tasks
```

### Source Code (repository root)

```text
infrahub_sync/
├── product_store/
│   ├── store.py          # list_runs, indexes, new columns, migrations, version/config queries
│   ├── models.py         # RunPage, version summary, run reference (store-side)
│   └── configs.py        # register writes actor/reason/name; paged list services
├── service/
│   ├── app.py            # GET /runs, GET /me
│   ├── service.py        # list_runs service method, binding validation (404s)
│   ├── config_routes.py  # paged /configs and /versions, name filter
│   ├── models.py         # bounded emitted variants for the new resources
│   ├── paging.py         # NEW: cursor encode/decode, filter digest, limit validation
│   └── orchestration.py  # tags in _title
├── client/
│   ├── models.py         # page and summary resources, PrincipalResource
│   └── client.py         # list_runs, iter pages, me, paged config lists
└── cli.py                # plan/diff alias, verify, runs list|artifacts|cancel, whoami, name resolution

tests/
├── product_store/        # list_runs ordering/filters/cursor, migrations (SQLite + PostgreSQL)
├── service/              # route contracts, 401/404/422, tags in test_run_titles.py
├── client/               # page parsing, iteration
└── cli/                  # commands, alias warning, name resolution

docs/docs/
├── reference/sync-http-api.mdx   # terms, routes, paging, CLI-to-API table
├── running-a-sync.mdx            # plan, runs list, whoami, Prefect tags
├── reference/cli.mdx             # regenerated
├── development/constitution.md   # diff → plan wording
└── (12 other pages)              # diff → plan
```

**Structure Decision**: extend the existing service, store, client and CLI modules. One new
module, `infrahub_sync/service/paging.py`, holds the cursor logic shared by the three list
routes.

## Delivery order

1. Store: indexes, columns, `list_runs`, version and configuration queries (US1, US2).
2. Paging module and `GET /runs` (US1).
3. Configuration and version list changes (US2).
4. `GET /me` (US4).
5. Prefect tags (US3).
6. Client methods, then CLI (US5).
7. Docs, constitution wording, `AGENTS.md` CLI list, changelog fragments.

Each step ships with its tests and leaves the tree green.

## Complexity Tracking

No constitution violations.
