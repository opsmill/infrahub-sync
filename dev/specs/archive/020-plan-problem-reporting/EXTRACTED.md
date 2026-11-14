# Extraction Record

**Extracted on**: 2026-11-14
**Extracted by**: speckit.opsmill.extract

## ADRs Created

- `dev/adr/0039-workers-publish-the-names-of-their-credential-variables.md` (from R15 and
  `plan.md`, "Complexity Tracking"). Its status is Proposed: the product owner accepted ADRs 0020,
  0021, 0022 and 0025 on 2026-11-12, and no document records an acceptance of this one.

ADRs 0020, 0021, 0022 and 0025 were written while the spec was implemented. Their Source lines
now name the archive path.

## Knowledge Updated

- `docs/docs/development/knowledge/plan-artifact.md` (from R9 and ADR 0022):
    - `format_version` is `4`; formats `2` and `3` are accepted for review only;
    - `report.jsonl` in the directory tree, and a row for it in the verdict table;
    - the manifest rows `report` and `dropdown_values_checked`;
    - new subsection "The report file".
- `docs/docs/development/knowledge/adapter-anatomy.md`, section "The read loop": a paragraph on a
  reference to a record of the same kind, and on references in a cycle (from R5)
- `docs/docs/development/knowledge/planned-write-and-apply.md`: a paragraph that says the schema
  fingerprint covers the choices of each mapped Dropdown attribute (from R4)
- `docs/docs/reference/durable-product-records.mdx`: the same fact, in the sentence on
  `destination_schema_fingerprint` (from R4)
- `docs/docs/running-a-sync.mdx`, section "Review the problems and the left-out records": two
  limits of the problem list (from `opsmill-implement-report.md`, "Known gap")
- `dev/adr/README.md` and `docs/docs/development/adr-index.mdx` (ADR index)

## Guidelines Updated

- `docs/docs/development/guidelines/secret-redaction.md`: new section "Build a plan problem's
  reason from its template" (from R13)

## Not extracted

- R2, R3, R6, R10, R11 and the `deletes_withheld_by_kind` part of R21: already ADR 0020.
- R12 and R13 (the failure record): already ADR 0021. R7 (`TransformError`) is covered by ADR 0021
  and by `docs/docs/development/guidelines/secret-redaction.md`.
- R9 (saved plan format 4): already ADR 0022.
- R22 (a warm source read admits its changed records): already ADR 0025 and
  `docs/docs/development/knowledge/incremental-and-cache.md`.
- R1, R14, R19 and R20: records of a measurement, of numbering or of a test plan.
- R8, R17 and the paged routes, `confirm_left_out`, the failure record fields and the
  registration response: already in `docs/docs/reference/sync-http-api.mdx` and
  `docs/docs/reference/cli.mdx`.
- R16 and the branch validation findings: already in
  `docs/docs/reference/durable-product-records.mdx` and `docs/docs/running-a-sync.mdx` ("Validate
  the registered version").
- R18 (the NetBox example): already in `examples/netbox_to_infrahub/README.md`.
- The problem types, the summary counts, apply with `--confirm-left-out` and the stopped `sync`:
  already in `docs/docs/running-a-sync.mdx`.
- The read loop, `load_record`, `RecordLoadError` and the removal of `continue_on_error`: already
  in `docs/docs/development/knowledge/adapter-anatomy.md`,
  `docs/docs/development/guidelines/writing-an-adapter.md` and
  `docs/docs/development/guides/adding-an-adapter.md`.
- The stopped `sync` that gives back its write admission: already in
  `docs/docs/development/knowledge/orchestration-prefect.md` and
  `docs/docs/development/knowledge/repository-tour.md`.
- The module map (`plan/problems.py`, `service/failure_cause.py`, `infrahub_errors.py`,
  `adapters/references.py`): already in `docs/docs/development/knowledge/repository-tour.md`.
- `data-model.md` and `contracts/`: covered by ADRs 0020 to 0022, the reference pages and the
  update of `plan-artifact.md`.
- `plan.md`, `quickstart.md`, `tasks.md`, `checklists/`, `critiques/`, `alignment-check.md`,
  `review-findings.md` and `opsmill-implement-report.md`: execution records.

## Open at archive time

`tasks.md` has no open task: all 83 tasks are ticked.

Tracked by an issue of `opsmill/infrahub-sync`, all three open on 2026-11-14:

- #417: the Slurp'it and IP Fabric adapters do not report a missing peer as a plan problem. A
  record whose peer is missing is skipped (Slurp'it) or written with no peer (IP Fabric). How to
  identify a missing peer for these two adapters is not decided.
- #418: the Infrahub adapter reads only the first `schema_mapping` entry of a kind, so the
  filters and transforms of later entries are not applied.
- #419: branch validation (`configs validate --branch`) has no rate or concurrency limit, and any
  authenticated caller can start it. The issue also covers which role may run a branch
  validation.

Issues that this spec addresses and that are still open on 2026-11-14: #369, #371 and #374.
Whether to close them is for the maintainer to decide.

Not tracked. The issue search was made when the extraction was planned, with the terms
`delete left out`, `deletes depend`, `withhold delete`, `left-out delete`,
`planned delete dependent`, `left out` and `delete in:title`. An issue under another title is
possible.

- A plan still lists the deletes of nodes that depend on a record left out of the plan, not
  tracked. A left-out record protects only the Infrahub node that has the record's own identity.
  A node with another identity can be listed as a planned delete, together with the deletes of
  the nodes that depend on it, for example the devices and interfaces in a rack. Planned deletes
  are never run (ADR 0004), so nothing is deleted. Whether to keep these deletes out of the plan
  is a product decision that is not made. `docs/docs/running-a-sync.mdx` states the limit.
- A record with two problems of different kinds lists only the first, not tracked. A record that
  has a wrong value type or a failed transform, and also a Dropdown value outside its choices,
  lists only the first problem. The record is still left out, and the second problem appears in
  the next plan. Two strict expected-failure cases of
  `tests/plan/test_left_out_walk.py::test_each_problem_of_one_record_is_listed_and_the_record_is_left_out_once`
  record it; both were present on 2026-11-14.
- The `worker_environments` table gains one row for each start of a worker process, not tracked.
  Read in the code on 2026-11-14: the row's key is the worker's name, which
  `service_worker_name` in `infrahub_sync/service/worker.py` makes with a new random part at each
  start. Each poll of a running worker replaces its own row and adds none. No statement in
  `infrahub_sync/` deletes a row of the table. Branch validation reads every row and keeps the
  rows that are recent enough, so old rows are not counted and are read each time. ADR 0039
  states this under Consequences.
- A reference within one kind that closes a cycle has no problem type of its own, not tracked. It
  is reported as `peer_not_found_in_source` with a reason that names the cycle. A dedicated type
  needs a spec change.
- Two PostgreSQL test files were not run during implementation, not tracked:
  `tests/integration/test_managed_write_guard_integration.py` and
  `tests/product_store/test_stopped_sync_admission.py`. The review's verdict on "a stopped `sync`
  gives back its write admission" rests on reading the code. Not checked again.
- Items of `spec.md`, "Out of Scope", left for version 3.0, not tracked: recording the structured
  cause in place of error text in the worker and Prefect logs, and removing the masking pass from
  published plans.

Review findings that `review-findings.md` marks as deferred (first review). The states of that
file are of 2026-11-12. Each is not tracked unless an issue is named:

- Which role may run a branch validation (high, partly fixed): tracked by #419.
- A peer that the read loads later is reported as `peer_not_found_in_source` (medium): fixed
  since for a peer of the same kind, which `load_records` admits on a later pass. The case of a
  peer of a kind later in the read order was not checked again.
- `sync --wait` exits 0 when the second read of the results fails (medium): fixed since. Read in
  the code on 2026-11-14: the command exits 1 whenever the outcome is
  `left-out-review-required` (`infrahub_sync/cli.py`).
- A `PlanManifest` at format 4 without `report` passes validation (medium): still present on
  2026-11-14. `parse_plan_artifact` requires the report, and every apply goes through it.
- The plan writer saves an empty report when `report` is `None`, and
  `dropdown_values_checked` is true by default (medium): still present on 2026-11-14.
- The table of dispatched runs in `tests/product_store/test_stopped_sync_admission.py` breaks two
  conditions in each case (medium). Not checked again.
- Each worker restart adds a row to `worker_environments` (low): still present, described above.
- Any authenticated caller can start the destination reads of a branch validation, with no rate
  limit (low): tracked by #419.
- The permission findings describe the account of the API's copy of the token, not the worker's
  import account (low). Not checked again.
- `_operation_identity` returns `None` when it cannot derive an identity, and a left-out record
  without an identity matches no operation (low). Not checked again.
- A filter that removes the peer of an optional relationship records no load report entry (low).
  Read in the code on 2026-11-14: `infrahub_sync/adapters/references.py` logs a warning and
  records no entry.
- A Dropdown attribute without stated choices is not checked, and the plan still records
  `dropdown_values_checked` as true (low). Not checked again.
- `stage_failure_resource` returns `None` when the server's failure record does not validate, and
  the CLI shows no failure (low). Not checked again.
- No model enforces that `left_out` equals `left_out_with_problem` plus `left_out_dependent`
  (low). Not checked again.
- `PlanProblem` does not enforce the fields that each problem type requires (low). Not checked
  again.
- The frozen models hold mutable dictionaries (low). Not checked again.
- An IP Fabric peer that the read cannot resolve is recorded as `wrong_value_type` with the value
  `None` (low). Not checked again; #417 covers the missing-peer reporting of this adapter.
- An ACI record without an ID has the record ID `""` and reference entries with source ID `None`
  (low). Not checked again.
- In the Nautobot and generic REST adapters, an entry in a list of peers that has no `id` becomes
  a peer with the ID `"None"` (low). Not checked again.
- A check of `tests/plan/test_problem_pass.py` passes without checking anything when `left_out`
  is empty (low). Not checked again.
- The docstring of `failure_record` says it does not raise, and its caller still guards the call
  (low). Not checked again.

Findings that the "Second review" table of `opsmill-implement-report.md` marks as deferred or
kept, none tracked and none checked again:

- The permission check ignores deny permissions. How Infrahub resolves a deny against an allow
  is not known.
- A destination adapter stores a placeholder for an optional peer that is not in the source.
- Each page of `runs plan --problems` parses the whole plan again.
- `configs validate --branch` reads the branch schema twice.
- A peer lookup that misses scans the whole kind each time.
- Two functions of `infrahub_sync/plan/keying.py` are used only by tests.
- A broad `except Exception` in `_structured_cause` is kept on purpose.
- Two different records whose raw `id` is the empty string count as one record read twice, so no
  duplicate is reported. Found while the tests were written, and not fixed.

Settled since the spec was written:

- The product owner accepted ADRs 0020, 0021, 0022 and 0025 on 2026-11-12.
- The AD-12 amendment in `opsmill/infrahub-sync-process`, named as an external dependency, merged
  on 2026-11-12.
- The sentence of `contracts/plan-artifact-format-4.md` that said the ADR 0007 fingerprint covers
  the report was corrected: the contract and ADR 0022 now say it does not.

## Archive

The spec directory moved to `dev/specs/archive/020-plan-problem-reporting/` as a historical
record.
