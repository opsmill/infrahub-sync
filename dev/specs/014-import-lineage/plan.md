# Implementation Plan: Lineage of the data an import writes

**Branch**: `014-import-lineage` | **Date**: 2026-10-22 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/014-import-lineage/spec.md`

## Summary

An import into Infrahub records the import account (the account behind its token) as the source of
every value it writes, sets an owner only when the package names one and only on values it creates,
stops before reading the source system when a named source or owner does not exist, sends the
protection flag only when `protect` is on, and shows in the plan who last changed each value it
proposes to update. Apply stops before any write when a named node is missing or when the import
account differs from the plan's.

Technical approach, from [research.md](./research.md):

- The default source comes from `AccountProfile` (`client.get_user()`), which needs no permission on
  accounts (R-3). The lookup of an account named after the source system is removed. The plan
  records the import account, and apply refuses a different one (FR-012).
- `source` and `owner` take the form `{kind, name}`, checked at registration through the Infrahub
  adapter's `validator` hook; `protect` is a new boolean destination setting.
- The destination read adds `include_metadata=True` to the existing SDK calls on Infrahub 1.7.0 or
  later. The number of requests does not change (R-2). On older servers the plan shows
  "not recorded" (R-1).
- The plan stores the last-change data in a new file, `plan/last_changes.jsonl`, plus two additive
  manifest fields covered by the checksum. The plan format version stays 3, and the product store
  database does not change (R-4).
- An HTTP 403 is told apart from a 401 through the status the SDK chains on its
  `AuthenticationError` (R-5).

## Technical Context

**Language/Version**: Python 3.10–3.13; the Sync service and worker run on 3.11–3.13.

**Primary Dependencies**: infrahub-sdk 1.23.2 (range `>=1.17,<2`, `pyproject.toml:18`), pydantic
v2, DiffSync through the Potenda engine, FastAPI and Prefect for the service. No new dependency.

**Storage**: plan artifacts in the run directory and the object store; the product store database
(`infrahub_sync/product_store/store.py:48-114`) is unchanged.

**Testing**: pytest through `uv run invoke tests.tests-unit`; integration tests marked opt-in and
run against the pinned Infrahub 1.10.6 stack.

**Target Platform**: Linux container (the Sync service and worker) and the CLI.

**Project Type**: CLI and web service with adapters (single Python package).

**Performance Goals**: no added requests to Infrahub for metadata. The growth of the destination
read time on a 10,000-node plan is not known; tasks measure it (R-2) and record it before the
feature merges. The sources set no threshold, so the result is information only.

**Constraints**: idempotent plans (metadata must not enter the diff); no secrets in causes; plans made
before the upgrade can still be reviewed, and apply refuses them (FR-012).

**Scale/Scope**: imports of about 10,000 nodes (the benchmark size in
`docs/docs/development/guides/netbox-benchmark-runner.md`).

## Constitution Check

*GATE: checked before Phase 0 and again after Phase 1. Result: pass, no violations.*

| Principle | How the design meets it |
|---|---|
| I. Read-only and dry-run by default | The last-change data is display only (D-2). `diff` and `runs plan` stay non-applying. `protect` is opt-in and off by default, which removes an implicit write behavior. |
| II. Idempotency and safety | Metadata is kept out of the DiffSync models, so a value a person changed does not create a spurious change. An unchanged value is not written. 401 and 403 are handled explicitly (R-5). A missing named node stops the run instead of writing with an empty lineage, and apply refuses before any write when a named node is missing or the import account differs from the plan's. |
| III. Adapter symmetry | Settings checks use the existing capability `validator` hook; writes stay in the Infrahub adapter and flow through Potenda. Docs updated in `docs/docs/adapters/infrahub.mdx`. |
| IV. Type safety | New settings and plan records are typed pydantic models; specific exceptions for a missing named node and a missing permission. |
| V. Test discipline | Unit tests for each refusal, each payload rule, each response code (401, 403, GraphQL denial), and the plan files; opt-in integration tests for the quickstart scenarios. |
| VI. Security and secrets | Causes quote the permission text only, never the token. No new credential. |
| Logging standard | Stops and refusals are logged with `structlog`, with the setting, kind, name or account names as context, never a secret. |
| VII. Simplicity | No new dependency, no new plan format version, no database change; one new plan file. |

Post-design re-check: pass. The new `PlanResource` fields are an optional API addition, and the
refusal of a bare-name `source` or `owner` is a configuration format change; both are listed as
governance gates in the spec.

## Project Structure

### Documentation (this feature)

```text
specs/014-import-lineage/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── infrahub-destination-settings.md
│   ├── plan-last-changes.md
│   └── run-errors.md
├── checklists/requirements.md
└── tasks.md             # created by /speckit-tasks
```

### Source Code (repository root)

```text
infrahub_sync/
├── configuration/capabilities.py   # Infrahub allowed settings + `protect`; validator for {kind, name}
├── adapters/infrahub.py            # lineage resolution (:713-741), metadata reads (:771-776, :815-817),
│                                   # payload rules (:1333-1345), last-change map
├── potenda/__init__.py             # 401 / 403 / GraphQL denial classification (:220-238)
├── plan/
│   ├── models.py                   # manifest fields `lineage`, `last_changes`; sidecar record model
│   ├── derive.py                   # changed fields of each update
│   ├── writer.py / reader.py       # write and read `last_changes.jsonl`, digest check
│   └── review.py                   # refuse a sidecar whose digest does not match
├── client/models.py                # PlanResource optional fields
├── service/flow.py                 # publish the new fields in the review document
├── product_store/bundle.py         # include the sidecar in the checkpoint if it lists files
└── cli.py                          # `runs plan` displays last changes and the owner notice

tests/
├── adapters/        # payload rules, lineage resolution, metadata map
├── configuration/   # registration refusals
├── plan/            # sidecar, manifest fields, checksum coverage
├── service/         # PlanResource fields
└── integration/     # quickstart scenarios against Infrahub 1.10.6 (opt-in)

docs/docs/adapters/infrahub.mdx                      # lineage section rewritten (FR-010)
docs/docs/development/knowledge/plan-artifact.md     # new file and manifest fields
changelog/                                            # news fragment, type `changed`; PR label `changes/minor`
```

**Structure Decision**: single Python package; the change stays in the existing modules listed
above.

## Items left open

- The oldest Infrahub version a V3 import supports is not known and stays out of this feature
  (R-1). The design works on any version.
- The cost of the metadata read is not measured yet (R-2). T004 and T056 measure it on the pinned
  stack; the result is information only.
- Closed (T001, 2026-10-22): the plan's destination read, `client.all(…, include=…)` at
  `infrahub_sync/adapters/infrahub.py:815-817`, carries `relationship_metadata` for the edges of
  every mapped cardinality-many relationship once `include_metadata=True` is added, with no added
  request. User Story 3 proceeds (R-2).
- Whether a direct Python plan with `full_extract=False`, which loads unchanged destination nodes
  from the previous snapshot, must load in full when metadata is requested is not decided. The
  shared execution surface and the Sync service always load in full (R-2). The User Story 3 tasks
  decide it.
- The import account's smallest permission set is not known yet (T005, R-7).
- Closed (product owner, 2026-10-22): apply refuses, before any write, a saved plan that records
  no import account (made before this feature) and asks for a new plan (FR-012). V3 is not
  released, and plans saved before the upgrade are already refused for other reasons.
- Closed (T002, 2026-10-22): apply reads nothing that tells which edges an update adds, and the
  plan does not record it. When an owner is named, apply reads the node's current peers once for
  each update with relationship references, and sends the owner only on edges to peers the node
  does not have (R-6).
- Closed (T003, 2026-10-22): the plan checkpoint copies a fixed list of files.
  `plan/last_changes.jsonl` is added to the members in `publish_plan_checkpoint`
  (`infrahub_sync/service/checkpoints.py:82-87`); rehydration needs no change (R-4).
- Whether the optional `PlanResource` fields need a change to the compatibility check
  (`infrahub_sync/service/compatibility.py`) is not checked yet (T041).
- Closed (product owner, 2026-10-22): the change that refuses a bare-name `source` or `owner`
  carries the release-intent label `changes/minor`. V3 is not released, so no released package
  breaks, and the v2 conversion writes the `{kind, name}` form. The news fragment type is
  `changed`.

## Complexity Tracking

No constitution violations.
