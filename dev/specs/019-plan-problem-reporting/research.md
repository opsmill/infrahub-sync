# Research: Plan problem reporting

Each item records a decision, why it was taken, and what else was considered. File and line
references are on `feature/v3-develop` at `ea83a248` unless they name the old branch
`009-plan-problem-reporting` ("the old branch"). Code on the old branch is a reference for ideas
and tests. It is not merged or cherry-picked.

## R1. Behavior today for the three unverified problem types (decision 11)

Verified in code on 2026-11-11.

- **Duplicate source identity.** Each adapter's loop builds the model and calls `self.add(item)`
  (for example `infrahub_sync/adapters/netbox.py:223-224`). DiffSync's `LocalStore.add` raises
  `ObjectAlreadyExists("Object {uid} already present")` when a different object with the same
  unique ID is stored (`diffsync/store/local.py:104-106`, DiffSync in `.venv`). Nothing in
  `infrahub_sync` catches it, so the source read stops and the plan fails. Confirmed.
- **Dropdown value outside its choices.** The runtime schema maps a `Dropdown` attribute to `str`
  (`infrahub_sync/runtime_schema/models.py:36-46`) and records no choices. No code in
  `infrahub_sync` reads Dropdown choices. The plan accepts any string, and Infrahub rejects the
  value during apply (#371). Confirmed.
- **Wrong value type.** A `Number` attribute is `int` (`runtime_schema/models.py:50`). Each
  adapter builds the model with `model(**data)`, and pydantic in lax mode refuses `2.5` for an
  `int` with the error type `int_from_float`. No adapter catches `ValidationError`, so the source
  read stops and the plan fails. Confirmed by reading; a test on the plan phase's fixture will
  pin it (task in Setup).

Decision: all three become plan problems through the admission step of R2, and the Dropdown
check needs the choices of R4.

## R2. Where a record that cannot be built is caught

Where each error is raised today:

- Transforms and peer resolution run inside the adapters' record conversion, before the model is
  built. The NetBox loop iterates the generator `_records_to_diffsync`
  (`infrahub_sync/adapters/netbox.py:120`, called at `:220-222`), which filters, transforms and
  resolves references, then builds `model(**data)` and calls `self.add(item)` (`:223-224`).
  Nautobot has the same generator (`adapters/nautobot.py:135`). A failed transform or a missing
  peer is raised inside the generator; a generator that raises cannot continue with the next
  record.
- `pydantic.ValidationError` is raised at `model(**data)`, one line before `add`.
- `ObjectAlreadyExists` is raised inside `add`.

So a helper that wraps only `model(**data)` and `add` would catch the wrong type and the
duplicate, but not the failed transform, the excluded peer or the missing peer.

Decision (as old 009 research R3, rebuilt on the current adapters): the base class owns the
per-record loop. `DiffSyncMixin.model_loader` gets a default implementation:

1. find the mapping entry for the kind
2. call the adapter's `list_records(model_name, element)` for the raw records
3. on the source side, apply the configured filters, record the IDs a filter removed (the NetBox
   bookkeeping of `netbox.py:208-227` moves here), and apply the transforms record by record
4. for each record, call `load_record(model, element, record)`

`load_record` is the admission step. It runs the record's transforms, calls the adapter's
`record_to_data(record, element, model)` (which resolves references through the helper of R5),
builds `model(**data)` and calls `self.add(item)`. On the source side it catches, per record:

| Exception | Recorded as |
| --- | --- |
| `pydantic.ValidationError` whose errors are all type errors | `wrong_value_type` |
| `diffsync.exceptions.ObjectAlreadyExists` | `duplicate_source_identity`; the store keeps the first record |
| `ExcludedRequiredPeerError` (new) | `required_peer_excluded_by_filter` |
| `PeerNotLoadedError` (new) | a dependent left-out record (R5) |
| `PeerNotFoundError` (new, source side) for a required relationship | `peer_not_found_in_source` |
| `TransformError` (R7) | `transform_failed` |

Any other exception, including a `ValidationError` for a missing required value, is raised as
`RecordLoadError` with the kind, the source ID and the field, and fails the plan with the failure
type `record_load_failed` (FR-018). On the destination side nothing is caught.

Each bundled source adapter keeps only `list_records` and `record_to_data`, taken from its
current `model_loader` and conversion function (NetBox, Nautobot, ACI, generic REST and Peering
Manager, Prometheus, Slurp'it, IP Fabric). IP Fabric today calls `update_or_add_model_instance`,
which overwrites a duplicate silently; through `load_record` it calls `add`, and a duplicate
becomes a problem. The Infrahub adapter keeps its own `model_loader`, because it also fills the
SDK store and hydrates peers, and calls `load_record` for each record.

`continue_on_error` exists today in `infrahub_sync/execution.py`, `utils.py`,
`potenda/__init__.py`, `adapters/nautobot.py` and `adapters/infrahub.py`. It skips records
without a count. It is removed: a skipped record without a problem entry breaks FR-001.

Rationale: one loop in one class is the only place where a per-record catch covers transforms,
reference resolution, model construction and `add` together (constitution principle III).

Alternatives considered:

- A helper that each adapter calls around `model(**data)` and `add`. Rejected: it misses errors
  raised inside the conversion generators (see above). Old 009's plan rejected it for the same
  reason ("a call per adapter was rejected").
- A model validator that returns a substitute instance. Rejected by old 009 research R3: pydantic
  discards a substitute returned by a wrap validator.
- Catch the error around the whole source read. Rejected: the read cannot continue with the next
  record.

## R3. How the plan decides what is left out

Decision: a separate problem pass after the operations are derived. It reads the load reports,
the source store, the destination schema record and the derived operations, and returns the
problems, the left-out records and the kept records. A pure selection step removes the
operations of left-out records and the relationship values of kept records. Neither store and
not the DiffSync diff are changed after the read. The walk follows required relationships at any
depth (FR-005); an optional relationship to a left-out record or to a peer not in the source makes
a kept entry.

Rationale: DiffSync compares the stores as read, so a plan of valid data has the same bytes as
today. The old branch's `plan/problems.py` and `plan/left_out_peer.py` are the reference for the
walk and its tests.

Alternative considered: removing records from the stores before the comparison. Rejected by the
product owner on 2026-10-15 for old 009, because it changes what DiffSync compares and can turn a
left-out record into a planned delete.

## R4. Dropdown choices

Decision: the runtime schema records the choices of each `Dropdown` attribute, and the choices
enter the consumed-schema fingerprint. The problem pass compares each mapped value with the
choices. A run with no destination schema snapshot (a direct Prefect flow run) cannot check; the
manifest records `dropdown_values_checked: false` and the summary shows it.

Consequence: adding choices changes the fingerprint, so `_require_planned_schema`
(`infrahub_sync/service/flow.py:281-300`) refuses to apply a plan saved before the upgrade with
`PlanSchemaChangedError`. R9 refuses format 3 plans anyway.

## R5. A peer that is not in the source

Decision: `SourcePeerUnresolvedError.absent` (`infrahub_sync/plan/errors.py:586-609`) and the
NetBox adapter's missing-peer errors (`infrahub_sync/adapters/netbox.py:271`, `:308`) become the
problem `peer_not_found_in_source`. One peer resolution helper is shared by the adapters that
resolve references while reading (NetBox, Nautobot, ACI, generic REST and the adapters built on
it). Through a required relationship the record is left out; through an optional one it is kept
without that relationship. The Nautobot adapter, which drops the reference with a warning today,
reports the problem too.

## R6. Identity problems

Decision: `DestinationIdentityCollisionError` (`plan/errors.py:333`) is no longer raised by the
plan. The collision check in `plan/keying.py` returns the colliding groups, which become
`destination_identity_collision` problems. `warn_missing_convergence_key` (`plan/derive.py`)
returns its groups as `identities_merge_in_destination` problems. Every record of a group is left
out. `destination_identity_ambiguous` is detected while the Infrahub adapter reads a kind in
full; the old branch's `plan/destination_ambiguity.py` is the reference.

## R7. A transform that fails for one record

Decision: the transform step raises a typed `TransformError` that holds the field and the
transform's position (`transforms[0]`), never the value or the error text. The admission step
records it as `transform_failed`.

## R8. Sync API and CLI surface for the lists (decision 8)

Decision: three paged routes, `GET /runs/{run_id}/plan/problems`, `/plan/left-out` and
`/plan/kept`, and three options on the existing `infrahub-sync runs plan` command: `--problems`,
`--left-out`, `--kept`, with `--type` and the existing `--kind` as filters. No `runs problems`,
`runs left-out` or `runs kept` command.

Rationale: `runs plan` is already the command that reads a saved plan
(`infrahub_sync/cli.py:1244-1257`), and its `--detail` and `--kind` options already list
operations. Three more top-level `runs` commands would split one plan review across four
commands. ADR 0024 rejected copying an artifact that names commands which do not exist; the
artifact of FR-028 names `runs plan <run_id> --problems` and `--left-out`, which will exist.

Alternative considered: re-add the old commands. Rejected for the reason above. A UI reads the
routes, not the commands.

## R9. Plan format 4 (decision 6)

Decision: `PLAN_FORMAT_VERSION = 4`, `SUPPORTED_FORMAT_VERSIONS = {2, 3, 4}`
(`infrahub_sync/plan/models.py:33-37`). Format 4 adds `plan/report.jsonl` (canonical encoding of
ADR 0001) and a `report` object in the manifest with its digest, size and counts. The plan
checksum covers the report digest. Everything format 3 holds stays: destination IDs on updates
(`DESTINATION_ID_FORMAT_VERSION = 3`), the `lineage` record and `plan/last_changes.jsonl` of
spec 014. Spec 018 adds nothing to the plan files: its read state lives on the stage execution.

A format 3 plan stays readable and reviewable. Its apply is refused with
`PlanFormatApplyUnsupportedError` (`plan/errors.py:106-122`), the refusal that already exists for
format 2, because a format 3 plan was never checked for problems and carries no left-out count to
confirm. This is the default proposed for open question 2 of the spec.

Alternative considered: an additive manifest key on format 3. Rejected, because a reader that
ignores the key would apply without the confirmation.

## R10. Apply confirmation

Decision: `ApplyRunRequest` (`infrahub_sync/client/models.py:123-129`) gains
`confirm_left_out: bool = False`, strict. `RunService.apply_run` checks it after the checksum
check (`infrahub_sync/service/service.py:397-420`) and refuses with 409
`left-out-confirmation-required`, the count in the message and in the error body, audited as
`refused-left-out-not-confirmed`. The engine's apply checks it again from the manifest, so a
direct Python caller cannot skip it. The client sends the field only when true, so a request body
without it keeps its shape.

## R11. A `sync` that stops after its plan (decision 1)

Decision: in the `sync` branch of the worker flow, after the plan is saved and before the write
guard admits a write, the flow reads the manifest's left-out count. When it is greater than zero,
the stage returns a result with `failure = {failure_type: "left_out_review_required",
next_action: ...}`, phase `planned` and outcome `left-out-review-required`.
`result_next_action` (`flow.py:888-897`) already reads that key for the last log line, and the CLI
reads the same field.

Write admission (found during the critique, 2026-11-11): a `sync` run holds a write admission from
the moment it is created (`RunService.create_run` reserves its receipt with
`admit_write=request.operation == "sync"`, `infrahub_sync/service/service.py:278-283`). Any later
write stage on the same run is refused with `run-execution-conflict`
(`infrahub_sync/product_store/store.py:2175`, message at `:310-314`). So without a store change,
the team could not apply a `sync` run that stopped after its plan, and FR-012 would fail.

Decision (old 009's design, rebuilt): the stop gives back the `sync` run's write admission. The
writeback that records the stop (phase `planned`, outcome `left-out-review-required`) also
deletes the run's `write_admissions` row, in the same transaction, and only when the stage
dispatched no destination operation. The old branch did this with
`ExecutionFinishWriteback(release_write_admission=True)` in `service/left_out_stop.py`, and the
behavior was checked on the preview stack on 2026-10-20 (apply refused without the confirmation,
`sync` stopped, confirmed apply admitted). After the release, `RunService.apply_run` reserves a
new write admission as for a `plan` run, so the existing rule holds: one write admission per run
that writes, and none after it.

Not yet verified on the current store: the store has changed since the old branch (specs 015 to
018). Task T033 writes the tests first:

- stopped `sync`, then a confirmed apply: admitted
- a second apply on that run: refused with `run-execution-conflict`
- an apply on a `sync` run that dispatched any operation: refused with `run-execution-conflict`
- the release and the stop's writeback commit together, or neither does

Alternatives considered:

- Append the apply under the `sync` receipt's existing admission. Not chosen: an apply request
  reserves its own receipt, and the store accepts a write execution only with the receipt that
  holds the admission (`_ADMITTED_APPEND_REFUSED`, `store.py:315-317`), so it needs a second
  change to receipts.
- Tell the team to create a new `plan` run and apply that. Rejected: it drops the plan the team
  reviewed, and old 009 FR-012 requires applying the stopped run.

The release changes the store's rule "a run that admitted its one write never admits another
stage" (`store.py:2160-2175`) for one case: a `sync` that stopped before any dispatch. ADR 0020
records it, and the product owner is asked to confirm it (spec, "Open questions").

## R12. The failure record (decisions 1 and 2)

Decision: `_failure_evidence` (`flow.py:805-847`) adds the fields of FR-014 from one function,
`failure_record(stage, exc, ...)` in `infrahub_sync/service/failure_cause.py`, which:

- maps the cause chain to a `failure_type` through a fixed table of error classes (for example
  `PeerNotFoundError` to `destination_peer_missing`, `PeerAmbiguousError` to
  `destination_peer_ambiguous`, the lineage refusals to `lineage_refused`,
  `DestinationSchemaUnavailableError` to `destination_schema_unavailable`, a 401 to
  `token_rejected`, a 403 to `permission_missing`, `ssl.SSLCertVerificationError` to
  `tls_certificate_rejected`, connection and timeout errors to `system_unreachable` and
  `system_timeout`), and to `internal_error` otherwise
- reads `system` from a typed `SideLoadError(role, adapter)` that replaces the plain `ValueError`
  wrapper the plan uses today for each side's read failure (a subclass of `ValueError`, so existing
  `except ValueError` paths keep working)
- reads Infrahub's `extensions.code`, `extensions.data.node_kind`, `extensions.data.field_name`
  and `message` from the GraphQL error of a rejected write, through `infrahub_sync/infrahub_errors.py`
- copies only those values, each capped in length and stripped of control characters

It never formats `str(exc)` of any error. The existing keys keep their meaning.

The decision record 0021 replaces the comment at `flow.py:834-836`: the record holds a class name
for the immediate cause (unchanged) and the allowlisted fields, including Infrahub's own message.

## R13. Credentials (decision 3)

Decision: prevention by construction. Credential values are resolved only into adapter and SDK
settings (`infrahub_sync/configuration/credentials.py`). No cause, problem reason, report entry or
plan field is built from connection settings or from error text (R12). Problem reasons are
rendered from a fixed template per type over source values, schema names and positions. The
masking pass (`redact`, `MIN_SECRET_LENGTH = 6` at `infrahub_sync/execution.py:128`) is neither
extended nor removed.

Tests: the old branch's `tests/service/test_failure_cause_credentials.py` and
`tests/plan/test_report_credentials_absent.py` are the reference. Each failure type is raised
with error text that holds a 5-character credential and a credential with `"`, `\` and `,`, and
the test checks that neither value appears in the failure record, the CLI output, the plan files
or the API responses.

## R14. Decision record numbers (decision 5)

`dev/adr/` holds 0001 to 0016, 0019 and 0024. ADR 0019 names a draft ADR 0017 held by another
pull request, and 0018 is next to it, so both are left free. This feature writes:

- **0020 Records with problems are left out at the plan stage.** Context: a plan stops at the
  first bad record (#369). Decision: FR-005 to FR-012; the AD-12 amendment in
  `infrahub-sync-process` is the product side. Consequences: plan format 4, apply confirmation,
  `sync` stop.
- **0021 A run's failure record holds a structured cause from allowlisted fields.** Replaces the
  rule "a class name, never a message" of `flow.py:834-836`. Context: spec 017 already records
  template texts and a quoted 403 message, so the code no longer matches the comment. Decision:
  FR-014, FR-019; Infrahub's own message is recorded for a rejected write; no other error text.
  Consequences: the comment and `docs/docs/development/guidelines/secret-redaction.md` change; the
  worker and Prefect logs keep the error text (3.0 follow-up).
- **0022 Saved plan format 4.** Decision: R9. Consequences: format 3 apply is refused.

The records cite only repository and public sources (repository convention).

## R15. `configs validate` (decision 9)

Decision:

- The CLI gains `--branch <name>` on `configs validate` (`infrahub_sync/cli.py:600`) and the route
  gains a `branch` query parameter (`infrahub_sync/service/config_routes.py:487`). With a branch,
  the service passes `DestinationSchemaOptions` to `validate`
  (`infrahub_sync/product_store/configs.py:894-929`), which already runs
  `collect_destination_schema_findings` (`infrahub_sync/configuration/schema_validation.py:183`)
  when given the option.
- Dropdown literals: a mapping `static` value of a Dropdown attribute is checked against the
  choices of R4: finding `destination-dropdown-value-not-in-choices`.
- Permissions and lineage names: the old branch's commit `a154b907` read the account's permission
  identifiers, the default branch, each mapped kind's namespace and name, and the source and owner
  names, and reported `destination-permission-missing`, `destination-lineage-node-not-found` and
  `-ambiguous`. That commit is the reference; spec 014 has since changed the lineage code, so it
  is rebuilt on the current `adapters/infrahub_lineage.py`.
- Worker credentials: each worker publishes the names (never values) of its set, non-empty
  `INFRAHUB_SYNC_CREDENTIAL_*` variables to a new product-store table `worker_environments`
  (`CREATE TABLE IF NOT EXISTS`, the store's existing pattern at
  `infrahub_sync/product_store/store.py:65-127`). Validation reports
  `credential-not-set-in-worker` per variable that no live worker holds, and the warning
  `worker-environment-unknown` when none has published. Compose passes unset variables as empty
  strings, so an empty value counts as unset.

Database change: one new table. It crosses the database change gate of the constitution and is
listed under Complexity Tracking.

## R16. Validation and plan agree on kinds without a writable HFID (SYNC-147)

Both paths already call `writable_convergence_reason` (`schema_validation.py:251`,
`plan/keying.py:316`, `:363`, `runtime_schema/worker.py:175`). Decision: a parametrized test runs
both paths on the same kinds and checks the same answer. The old branch's test T088 is the
reference.

Finding of 2026-11-11 (T064, chunk 13): the two paths call the same rule on different fields.
Validation passed the mapped fields as the identity; the plan keys each create on the
operation's identity, which holds only the fields the mapping selects as `identifiers`. So
3 of the 8 kinds of T064 disagreed: those where the mapping maps a key component but does
not select it. Validation accepted them and the plan refused their creates.

Decision (T083): the plan is what writes, so validation changes to the plan's fields. It
selects the identifiers with `get_identifiers` (the mapping's `identifiers`, or else its
mapped unique attributes, the same function the runtime model builder uses) and judges only
the selected identifiers that are also mapped. The plan side does not change. The registered
worker's construction check (`runtime_schema/worker.py`) still judges the mapped fields; it
accepts more than validation now does, and the plan refuses the creates it lets through, so a
run never writes what validation refuses.

## R17. Register response without redacted content (SYNC-107)

Decision: `POST /configs` and `POST /configs/{config_id}/versions` return a version reference with
`package_checksum` and no `declared_content`. `GET` of a version keeps returning content. The
stored response body of the idempotency receipt is the reference body, so a replay matches.

## R18. The NetBox example (#374)

Decision:

- Device status: a transform in `examples/netbox_to_infrahub/package.yml` translates NetBox
  statuses that are not choices (for example `planned`, `staged`, `failed`, `inventory`,
  `decommissioning`, `offline`) to a choice, and the README lists the translation. The old
  branch's transform (R16 there) is the reference. The exact choices of the schema library's
  Dropdown are read from the pinned schema during implementation.
- Rack position `2.5`: listed as `wrong_value_type`. Changing the Infrahub schema is outside this
  repository, and rounding would write a wrong position.
- Two devices with the same name at two sites: the mapping identifies a device by `location` and
  `name` (`package.yml:234-235`), where `location` maps the rack. Whether the destination
  human-friendly ID of `DcimDevice` also holds the location is not known; if it does not, the pair
  is `identities_merge_in_destination` and both are listed. Verified on the pinned `demo` dataset
  during implementation, and the README states the result.

## R19. Plan time

The time the read loop changes and the problem pass add at tier M is not known. Spec 018 records
each side's read time; the plan step's total time is in the run record. Decision: measure a tier M
plan before and after on the `from-netbox` check and record both in the implementation report. No
target is set, because the spec sets none.

## R20. Stopped apply and the next plan

Decision: a test stops an apply at a known operation with a fake destination that rejects one
write, then plans again on the same stores and checks FR-022 and SC-004. The old branch's
`tests/plan/test_replan_after_stopped_apply.py` is the reference. The run record gains the total
operation count and the failed operation's action, kind and identity, read from the plan by
`failed_operation` ID.

## R21. Fields of old 009's data model and format 4 contract

Compared field by field with old `data-model.md` and `contracts/plan-artifact-format-4.md`.

- **`deletes_withheld_by_kind`: kept.** A left-out record whose identity cannot be computed (the
  load could not build it) cannot be matched to its Infrahub node. If the plan computed deletes
  for its kind, that node would look absent from the source and be planned as a delete, which
  breaks FR-008. So the plan computes no delete for such a kind, nor for the kind of a
  `destination_identity_ambiguous` identity (the whole kind, as data-model.md states, not only
  that identity), and the summary counts these records per kind in
  `deletes_withheld_by_kind`. The CLI prints one line per kind ("deletes not computed for
  `<kind>`: N records without identity").
- **Reserved `cleared_relationships` field: not carried over.** Old 009 reserved it on updates
  for old spec 010 (second sync), with a refusal `plan-feature-not-supported`. Old spec 010 is not
  on `feature/v3-develop` (the current `dev/specs/010-prefect-from-sync-image/` is another
  subject), and no current specification asks for it. A later format can add it.
- **Format 3 apply.** Old 009's contract refused format 2 and 3 apply with "plan again before
  applying", and its assumptions accepted that for the testing release. Kept: R9.
- **Old refusal codes `new-plan-required` and `plan-format-not-applicable`: not carried over**
  (decision 4; R9 uses the existing `PlanFormatApplyUnsupportedError`).
- **`RunFailureCause.summary` and `error_class`: mapped** to the existing `cause` and
  `error_type` keys of `<stage>_failure` (decision 1).
- **`apply_progress`: mapped** to the existing apply record keys plus `operations_total` and
  `failed_operation_detail`.
