# Extraction Record

**Extracted on**: 2026-11-14
**Extracted by**: speckit.opsmill.extract

## ADRs Created

- `dev/adr/0032-run-and-configuration-lists-page-with-a-cursor.md` (from R1, R2, R4, R5, R6, R8)

## Knowledge Updated

- `docs/docs/development/knowledge/repository-tour.md`:
    - section "The Sync service and its worker": the cursor of `service/paging.py` (from R2,
      R8);
    - section "Product storage": the three paged store reads, the columns a list reads, and the
      list indexes (from R1, R3, R5, R6, R7).
- `docs/docs/development/knowledge/orchestration-prefect.md`, section "Registered service
  integration": the tags the API sets when it creates a flow run (from R9, revised decision).
- `dev/adr/README.md` and `docs/docs/development/adr-index.mdx` (ADR index)

## Guidelines Updated

None. The rule for a new list of rows that are added while a client pages through them is in the
last Decision paragraph of ADR 0032. No guideline page covers the Sync API or the product store.

## Not extracted

- R1 (`destination_branch` on a run), R5 (`registered_by`, `registration_reason`), R6 (the
  configuration name and its exact-match filter) and R7 (`latest_run`, `latest_applied_run`), as
  API behaviour: already in `docs/docs/reference/sync-http-api.mdx` ("List runs and
  configurations") and `docs/docs/running-a-sync.mdx`.
- R2 and R8 as API behaviour (page shape, limits, `422 invalid-cursor`, removal of `offset`):
  already in `docs/docs/reference/sync-http-api.mdx`.
- R9 as operator behaviour (the four tags and how to filter by them): already in
  `docs/docs/reference/sync-http-api.mdx` ("Find a run's flow runs in Prefect") and
  `docs/docs/running-a-sync.mdx`.
- R10 (`GET /me`, `whoami`): already in `docs/docs/reference/sync-http-api.mdx`,
  `docs/docs/reference/cli.mdx` and `AGENTS.md` ("CLI Commands").
- R11 (`plan` replaces `diff`, the new commands, a configuration found by ID first and then by
  name): already in `docs/docs/reference/cli.mdx`, `docs/docs/running-a-sync.mdx`,
  `docs/docs/reference/sync-http-api.mdx` and `AGENTS.md`.
- R12 (constitution wording): already applied in `docs/docs/development/constitution.md`.
- `contracts/http-api.md`: already in `docs/docs/reference/sync-http-api.mdx`.
- `contracts/cli.md`: replaced by the generated `docs/docs/reference/cli.mdx`.
- `data-model.md`: the public fields are in `sync-http-api.mdx`; the stored columns and indexes
  are in `repository-tour.md`.
- `critiques/critique-20261104.md`: E1 (start times stored in UTC) and E3 (`runs cancel` asks
  for `--yes`) are in the code; E2 was replaced by tags set when the flow run is created; P1 was
  reversed by the clarification of 2026-11-04; P2 (ID before name) is in the CLI reference.
- `alignment-check.md`, `plan.md`, `quickstart.md`, `tasks.md`, `opsmill-implement-report.md` and
  `checklists/requirements.md`: execution records.

## Open at archive time

`tasks.md` has no open task: all 44 tasks are ticked.

- Which product card owns this work is not known, not tracked (`spec.md`, "Open Questions").
- `contracts/cli.md` is behind the CLI, not tracked. `opsmill-implement-report.md` (section 5,
  decision 3) says the CLI goes beyond the contract and the contract was not updated.
  `docs/docs/reference/cli.mdx` is generated from the code and is the current reference.
- Tests that were changed but did not run locally, not tracked. Whether CI ran them is not
  known:
    - `tests/preview/test_config_lifecycle.py`, `tests/preview/test_cli_client.py`,
      `tests/preview/test_run_review.py` and `tests/preview/test_schema_drift.py`, which need
      the preview stack;
    - `tests/compose/test_configuration_free_startup.py`,
      `tests/compose/test_bootstrap_idempotence.py` and `tests/compose/lifecycle.py`, which
      need Docker Compose.
- The purposes of the first four list indexes come from the query plans of research R3 (SQLite
  3.45.1 and PostgreSQL 16.15) and from the comment above `_RUN_LISTING_INDEXES`. The query
  plans were not run again for this extraction, not tracked.
- Review findings that `opsmill-implement-report.md` marks as deferred, not tracked. The state
  of each was read in the code on 2026-11-14:
    - Open: the unpaged `list_configurations` and `list_configuration_versions` store methods
      remain in `infrahub_sync/product_store/store.py`. Only tests and the Compose test scripts
      call them.
    - Open: `_resolve_config` in `infrahub_sync/cli.py` treats every 404 as "try the value as a
      name", so `configs show <unknown-id>` exits 2 and not 1.
    - Open: `docs/docs/development/constitution.md` (principle on the non-mutating path) names
      `configs` inspection, `runs plan` and `plan` as safe to run without approval. It does not
      name `verify`, `runs list` or `whoami`, and gives no rule for `runs cancel`.
    - Open: the `iter_*` methods of `infrahub_sync/client/client.py` are generators
      (`_iterate`), so they check their arguments on the first iteration, not when they are
      called.
    - Open: `list_configs` and `list_versions` in `infrahub_sync/service/config_routes.py`, and
      `list_runs` in `infrahub_sync/service/service.py`, read `items[-1]` when the store reports
      a further page, and assume that such a page is never empty.
    - Open: in `infrahub_sync/product_store/models.py`, `latest_version` is optional, and the
      `phase` and `outcome` of `RunReference` are plain strings.
    - Open, and larger: `infrahub_sync/cli.py` had 1,060 lines when the finding was written and
      has 2,154 now. Whether `diff` still copies the options of `plan`, and whether the listing
      code is still repeated, was not checked.
    - Partly done: the finding says the keyset paging code and the cursor code are each repeated
      three times. The cursor code is shared in `infrahub_sync/service/paging.py`, and the store
      has the shared helpers `_read_page` and `_keyset_parameters`. Whether any copy remains was
      not checked.
    - Not checked: whether the configuration and version pages are built through the strict
      response models the server uses for the resources it returns.
    - Accepted risk: runs stored before this change by a service whose clock was not UTC are
      not normalized, so their order in a list can be wrong. ADR 0032 records it as a
      consequence.
    - No longer applies: "When Sync cannot read a flow run's tags, it drops tags an operator
      added to that flow run." Sync sets the tags when it creates the flow run, and does not
      read or update tags afterwards (`_LabelledCreationClient` in
      `infrahub_sync/service/orchestration.py`).

Statements of the spec documents that the code no longer matches:

- R2 describes a cursor tied to its filters. The cursor is also tied to its list: each list adds
  its own name (`runs`, `configs` or `versions`) to the filters it digests, so a cursor from one
  list is refused by another.
- R3 lists five indexes. A sixth exists, `product_runs_config_started (config_id, started_at,
  run_id)`, added by the review for a run list filtered by configuration only.
- R9 has two decisions. The first (set the tags with an update after submission) is replaced by
  the revised decision in the same section (set the title and tags when the flow run is
  created). The code follows the revised decision.
- R10 lists the fields of `GET /me`. The route also returns `approval_required`, added later.
- `alignment-check.md` and `spec.md` (Assumptions, critique P1) say the run list has no
  destination branch. The clarification of 2026-11-04 reversed that: `destination_branch` is
  stored on the run and returned (R1).

## Archive

The spec directory moved to `dev/specs/archive/010-run-discovery-api/` as a historical record.
