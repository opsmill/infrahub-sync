# Implementation Plan: Register a configuration package from the web interface

**Branch**: `025-register-config-web` | **Date**: 2026-11-18 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `dev/specs/025-register-config-web/spec.md`

## Summary

An operator drops a configuration package in the web interface, reviews it, validates it against
the destination before it is stored, and registers it as a new configuration or as the next version
of one. The Sync API gains a route that validates a package without registering it
(`POST /api/configs/validate`), which parses the file text on the server and returns the package as
the store would keep it. The web interface gains two drop areas and a review step built on that
route and the existing registration routes. A separate story adds a "registered without validation"
mark on a version, for registrations made while the destination cannot be reached.

## Technical Context

**Language/Version**: Python 3.11 to 3.13 for the Sync service (3.10 for the CLI and the client);
TypeScript in `frontend/`.

**Primary Dependencies**: FastAPI and Pydantic (service), Typer (CLI), PyYAML (`yaml.safe_load`,
already used by the CLI), React with TanStack Query and Router, `openapi-fetch`, `@infrahub/ui`. No
new dependency.

**Storage**: the product store (SQLite or PostgreSQL). One new table,
`configuration_versions_unvalidated`, created with `CREATE TABLE IF NOT EXISTS` (research R8).

**Testing**: pytest (unit tier: `uv run invoke tests.tests-unit`); `biome check` and
`tsc --noEmit` for the web interface, which has no test runner (research R12).

**Target Platform**: the Sync service container and a current desktop browser.

**Project Type**: web service with a CLI client and a single-page web interface.

**Performance Goals**: the review step shows the validation result within the time the existing
validate route takes for the same package and branch; no added network round trip beyond one
validation and one latest-version read.

**Constraints**: no secret value in any response, finding or log (FR-005, spec 007 FR-038); no user
or actor named in the web interface (`AGENTS.md`, "Sync frontend"); no new dependency.

**Scale/Scope**: one package per drop; packages of the size the CLI already registers.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design.*

Constitution: [`docs/docs/development/constitution.md`](../../../docs/docs/development/constitution.md).

| Principle | Assessment |
|---|---|
| I. Read-only and dry-run by default | Pass. The new route is read-only and stores nothing. Registration keeps its administrator rule and is reached only through an explicit Register click after a review. |
| II. Idempotency and safety | Pass. Registration keeps its `Idempotency-Key`; the web interface reuses one key per review step (FR-017). Registering an identical package stays a no-op (`created: false`). |
| III. Adapter symmetry | Not affected. No adapter changes. |
| IV. Type safety and explicit contracts | Pass. New request and response models are typed Pydantic models; the HTTP contract is in [contracts/sync-api.md](contracts/sync-api.md); `types.generated.ts` is regenerated from the OpenAPI document. |
| V. Test discipline | Pass. Unit tests for the shared validation function, the new route (parsing, v2 refusal, reachability, parity with the registered route), the CLI command and the mark in both store dialects. |
| VI. Security, secrets, input boundaries | Pass. The new route applies the same secret redaction as the existing validate route. `document` is parsed with `yaml.safe_load` only. The route is authenticated like the existing validate route. |
| VII. Simplicity | Pass. One shared validation function instead of two copies; a new table instead of the first column migration; server-side parsing instead of a second YAML parser in the browser. |
| Command-line sanity and docs | Pass. `configs check` gets `--help` text; CLI reference regenerated; user docs updated for the web interface and the CLI; a changelog fragment and an ADR are added. |

No violation; Complexity Tracking is empty.

Post-design re-check: pass. The design adds one route, one CLI command, one store table, one request
field and one resource field, and changes no permission.

## Project Structure

### Documentation (this feature)

```text
dev/specs/025-register-config-web/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── sync-api.md
│   └── web-interface.md
├── checklists/requirements.md
└── tasks.md             # created by the tasks phase
```

### Source Code (repository root)

```text
infrahub_sync/
├── product_store/
│   ├── configs.py           # shared _collect_report(); new check_package(); mark on register/create_version
│   └── store.py             # configuration_versions_unvalidated table; read/write the mark
├── configuration/
│   └── schema_validation.py # expose the destination read outcome (research R3)
├── service/
│   └── config_routes.py     # POST /configs/validate; registered_without_validation on mutations
├── client/
│   ├── models.py            # request/response models for the check and the mark
│   └── client.py            # check_package()
└── cli.py                   # configs check

frontend/src/
├── api/
│   ├── types.generated.ts   # regenerated
│   └── actions.ts           # useCheckPackage, useRegisterConfig, useCreateConfigVersion
└── features/configurations/
    ├── PackageDropArea.tsx  # drop area plus "choose a file"
    ├── PackageReview.tsx    # review step (modal)
    ├── package-review.ts    # review state, default branch, reachability, messages
    ├── ConfigurationsPage.tsx  # adds the "register a configuration" drop area
    └── ConfigurationPage.tsx   # adds the "add a version" drop area; shows the mark

tests/
├── product_store/test_configs_service.py   # check_package parity, mark
├── product_store/test_configuration_unvalidated_mark.py
├── service/test_config_routes.py           # the new route and the mutation field
└── cli/ or tests/test_cli_*.py             # configs check (follow the existing CLI test layout)

docs/docs/                 # web interface and configuration package pages; CLI reference regenerated
dev/adr/0051-a-package-can-be-validated-before-it-is-registered.md  # 0048 to 0050 may be used by other branches; take the next free number at merge
changelog/+register-config-web.added.md
```

**Structure Decision**: extend the existing configuration modules in place. The web interface work
stays inside `frontend/src/features/configurations/`, next to the pages it changes.

## Implementation order

1. Shared validation function and `check_package` in the product store (User Story 1).
2. `POST /configs/validate`, client method, `configs check` (User Story 1, shippable alone).
3. Regenerate `types.generated.ts`; web interface drop areas and review step for a new version
   (User Story 2), then for a new configuration (User Story 3).
4. The mark: store table, request field, resource field, web interface button and display (User
   Story 4). Without this step, the review step keeps Register disabled when the destination cannot
   be reached (FR-022).
5. Docs, ADR, changelog fragment.

## Risks

- **Spec 022, local accounts, is in progress on branch `022-local-accounts`.** It replaces the
  token list with accounts that are administrators or not (its decision D-5) and changes how
  records hold the actor (its ADR 0050). This feature depends only on the administrator flag that
  `whoami` returns, which 022 keeps, and adds no actor column. Whichever branch merges second
  resolves conflicts in `config_routes.py`, `store.py` and the web interface's token handling.
- **ADR numbers**: 0049 and 0050 are used on `022-local-accounts`; 0048 is not used on any local
  branch, but may be reserved. This plan names 0051 and takes the next free number at merge.

## Complexity Tracking

None.
