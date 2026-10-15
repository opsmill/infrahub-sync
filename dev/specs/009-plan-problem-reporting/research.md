# Research: Plan problem reporting

Each section states a decision, why it was chosen from the code at commit `d9e83cb2`, and the
alternatives that were rejected. Sizes and timings come from the existing benchmark records in
`infrahub-sync-process/product/metrics.md` and `validation.md`. No new measurement was run.

## R1. Where each of the six problem types is found today

Decision: each problem type is detected where the data that proves it first exists, and each
detection site records a problem instead of raising.

| Problem type (stable name) | Where it stops a plan today | Where the plan detects it |
| --- | --- | --- |
| `dropdown_value_not_in_choices` | Not detected. Infrahub rejects the write during apply (#370, #371; `runtime_schema/models.py:46` types a Dropdown as `str`) | Engine, after the source load, against the destination schema record |
| `duplicate_source_identity` | `self.add(item)` in each adapter's `model_loader` raises DiffSync's "already present" (`adapters/netbox.py:224`, `nautobot.py:254`, `aci.py:287`, `genericrestapi.py:145`, `prometheus.py:511`, `slurpitsync.py:281`) | Shared record admission in `DiffSyncMixin` |
| `destination_identity_collision` | `refuse_destination_identity_collisions` raises `DestinationIdentityCollisionError` (`plan/keying.py:523-557`) | Same function, changed to return collisions |
| `wrong_value_type` | Pydantic refuses the model when the adapter builds it, for example `int_from_float` on `DcimDevice.position` (#374) | Shared record admission in `DiffSyncMixin` |
| `required_peer_excluded_by_filter` | `ValueError("Configured filter excluded all peers for required relationship …")` (`adapters/netbox.py:270-275`, `306-312`) | Adapter reference resolution, through a typed exception the admission step catches |
| `identities_merge_in_destination` | A warning only (`_warn_identity_finer_than_destination_key`, `plan/derive.py:791-837`; #166) and `refuse_source_identity_collisions` (`plan/keying.py:67-122`) | Same functions, changed to return the merge groups |

Rationale: every one of these sites already holds the record, the kind and the reason. Moving
detection elsewhere would need the data again. `potenda/__init__.py:488-501` wraps every load
failure into one `ValueError`, so today the first problem ends the whole load; collecting at the
admission step is the only place that sees each record once.

Alternatives considered:

- A separate validation pass over raw source records before the load. Rejected: the adapters
  turn raw records into model data (`netbox_obj_to_diffsync`, `adapters/netbox.py:229-315`), so a
  separate pass would repeat each adapter's mapping logic.
- Keep raising, and retry the plan without the failing record. Rejected: one plan per problem
  is what #369 reports (five configuration versions on 2026-10-05).

## R2. Shared record admission for every bundled source adapter

Decision: add `DiffSyncMixin.admit_record(model, data, *, source_record)` in
`infrahub_sync/__init__.py` (the mixin is at line 300). It builds the model, adds it to the store
and, on the source side only, turns these exceptions into problems instead of raising:

- `pydantic.ValidationError` becomes `wrong_value_type`, naming the field and the value;
- `diffsync.exceptions.ObjectAlreadyExists` becomes `duplicate_source_identity`, naming both
  records;
- the new `ExcludedRequiredPeerError` becomes `required_peer_excluded_by_filter`;
- the new `LeftOutPeerError` makes the record a dependent left-out record (required
  relationship) or a kept record (optional relationship), see R4.

Each bundled adapter's `self.add(model(**data))` call (the six sites in R1) is changed to call
`admit_record`. The IP Fabric adapter calls `update_or_add_model_instance`
(`adapters/ipfabricsync.py:104-105`), so a duplicate there overwrites the earlier record without
an error; it is changed to call `admit_record` too, which makes duplicates visible. The Peering
Manager adapter (`adapters/peeringmanager.py`) is checked in the tasks for which loader it uses. The destination side keeps raising as it does today, because a bad destination
record is not a source problem.

The NetBox bookkeeping of filter-excluded peer IDs (`_filtered_peer_ids`,
`adapters/netbox.py:34-59`) moves into the mixin, so that the NetBox, Nautobot, ACI and generic
REST adapters, which resolve references while loading (`nautobot.py:286-300`, `aci.py:332-362`,
`genericrestapi.py:217-233`), raise the same typed exceptions. The Prometheus, Slurp'it and IP
Fabric adapters resolve no references while loading; for them only the type and duplicate checks
apply. The task list confirms this per adapter with a test.

Rationale: constitution principle III requires one contract for every adapter. One admission
method gives every bundled adapter the same checks with a one-line change at each call site.

Alternatives considered: a decorator around `model_loader`. Rejected because the loader loops over
many records, and a decorator cannot continue the loop after a failure.

## R3. The Dropdown check needs the choices in the destination schema record

Decision: the schema read keeps the names of a Dropdown attribute's choices.

- `_attribute_shape` (`configuration/capabilities.py:345-356`) adds `"choices": [names]` for an
  attribute of kind `Dropdown`, and nothing for other kinds.
- `NormalizedAttribute` (`runtime_schema/domain.py:29-47`) adds `choices: tuple[str, ...] | None`.
- `_member_semantics` (`runtime_schema/projection.py:38-58`) adds `choices` for a mapped Dropdown
  attribute. This changes the consumed-schema fingerprint of every configuration that maps a
  Dropdown attribute.
- After the source load, the engine checks every mapped Dropdown value against those choices.
  A `None` value on an optional attribute is not checked.

Rationale: the spec's assumption accepts that plans saved by an earlier release must be planned
again (#371). The fingerprint change makes that refusal automatic: `PlanSchemaChangedError`
already refuses an apply whose recorded fingerprint differs from the live one.

Alternatives considered: read the choices only during the check and leave the fingerprint alone.
Rejected: a change to the choices in Infrahub would then not invalidate a reviewed plan, and the
apply could still write a value the reviewer was told was valid.

## R4. Leaving out records and their dependents (AD-12 plan stage)

Decision: the plan builds a left-out index in three steps, and filters the plan with it.

1. Load time. A record that fails admission is not added to the source store. Its raw source ID
   is recorded per kind. Because the adapters load kinds in dependency order
   (`SyncConfig.compute_order_and_tiers`, `infrahub_sync/__init__.py:187`), a later record whose
   peer was left out finds the peer's raw ID in that index:
   - through a required relationship, the adapter raises `LeftOutPeerError` and the record is
     left out as a dependent, naming the peer;
   - through an optional relationship, the adapter leaves the field empty, and the record is
     kept and listed with the relationship and the peer.
2. After the load and after derivation. Records with a Dropdown or identity problem are already
   in the source store. The engine walks the store's reference fields once to find every record
   that refers to them, and repeats until no new record is found. A reference through a required
   relationship (destination schema `optional: false`) leaves the record out. A reference
   through an optional relationship keeps it.
3. Filtering. Every create and update whose kind and source identity are in the left-out index is
   removed. Every delete that `derive_deletes` proposes for a destination object whose identity
   matches a left-out record is removed too, so a left-out record that already exists in Infrahub
   stays as it is (spec edge case and FR-008).

A kept record is written without the relationship:

- On a create, the relationship is absent from the operation (ADR 0001: an absent
  `relationships` entry carries no value).
- On an update, the relationship is removed from the operation, which leaves the destination
  relationship unchanged, as `_warn_dropped_cardinality_one_clear` already does for a null peer
  (`plan/derive.py:558-587`). An update whose only difference was that relationship is dropped.
- For a relationship of cardinality many, the whole relationship is removed from the operation,
  not only the left-out peer. Removing one peer from the set would make the replace-set write
  (ADR 0012) remove that peer from the Infrahub node.
- A relationship that is a component of the destination identity cannot be omitted; the record is
  left out as a dependent instead, as for a required relationship.

Rationale: the product owner decided on 2026-10-15 that an optional reference keeps the record
(spec FR-005). Filtering operations after derivation keeps `operations_from_diff`
(`plan/derive.py:590-682`) and `derive_deletes` unchanged except for one filter argument, so
ADR 0001's operation identifiers and the checksum rules are untouched.

Alternatives considered:

- Remove left-out records from both stores before the diff. Rejected: derivation resolves peers by
  probing the source store (`_resolve_references`, `plan/derive.py:341`), so a kept record's
  update would fail with `SourcePeerUnresolvedError`.
- Leave out the whole plan when any record has a problem. Rejected by the spec (FR-001, FR-005).

## R5. Duplicate records and identity collisions leave out every record involved

Decision: for `duplicate_source_identity`, the record already in the store is removed from the
plan as well as the one that was refused. For `destination_identity_collision` and
`identities_merge_in_destination`, every record in the group is left out. Each problem names the
other records (FR-003).

Rationale: the spec assumption says the plan cannot tell which record is correct.

## R6. The plan report and its size

Decision: the problems, left-out records and kept records are written to a third file in the
plan directory, `plan/report.jsonl`, in the canonical encoding of ADR 0001, one entry per line,
in a stable order: problems by problem type, kind, source identity; then left-out records by
kind and identity; then kept records. The manifest records the file's SHA-256 digest, its size
and the counts, so the plan checksum covers the report (R7).

Size estimate from the benchmark records, not measured:

- Tier L's first-sync plan is 69,757,845 bytes for 87,815 objects (metrics.md, "A v3 sync of
  tier L cannot complete"), about 794 bytes per object. Tier M has 10,207 objects (metrics.md,
  "What was measured"), so a tier M plan is about 8.1 MB, below the 16 MiB client limit.
- A report entry holds a kind, an identity, a type and a reason bounded to 512 characters, so
  each is at most about 1 KB. In the worst case where every tier M record has one problem, the
  report is about 10 MB. In the NetBox demo on 2026-10-05, five problems were found (#369).
- The report is returned in pages (contract `sync-api-plan-report.md`), so its size does not
  reach the client limit.

Planning time: detection adds one check per record and one pass over references. The tier M
first-sync plan step takes 4 min 33 s and the no-change plan step about 19 to 21 min
(metrics.md, "Where the v3 time goes"). VAL-1 measured 88,000 synthetic objects compared and
planned in about 5 seconds (validation.md), so the added in-memory passes are expected to be small
next to the load. Spec 010 measures the plan step by phase; this plan adds a `problems` phase
to that measurement through spec 010's mechanism and runs no measurement of its own.

## R7. Decision record 0022 (draft): saved plan format 4

Name: `dev/adr/0022-saved-plan-format-4-records-left-out-records.md`.

Draft content:

> **Status**: Proposed. **Date**: 2026-10-15. **Source**: `dev/specs/009-plan-problem-reporting`.
>
> **Context.** A plan can now leave out records (ADR 0020). Apply must then require the person
> to confirm the left-out count. ADR 0001 lets a later outcome add a manifest key without a
> format bump, because unknown keys are tolerated on read. For this key that tolerance is the
> wrong posture: a reader that does not know the key would apply the plan without asking for
> the confirmation.
>
> **Decision.** The plan format becomes 4.
>
> - The plan directory holds a third file, `report.jsonl`, written after `operations.jsonl` and
>   before `manifest.json`, in the same canonical encoding. `manifest.json` stays the commit
>   point.
> - The manifest gains a `report` object: `digest` (SHA-256 of `report.jsonl`), `size`,
>   `problems`, `left_out`, `left_out_with_problem`, `left_out_dependent`, `kept`. The checksum
>   rule of ADR 0001 is unchanged, and it covers the report through this digest.
> - `PlannedOperation` is unchanged, so operation identifiers and their bytes are unchanged.
> - Reading and review accept 2, 3 and 4. Apply accepts 4 alone, and refuses 3 with
>   `PlanFormatApplyUnsupportedError` and the instruction to plan again, as the format-3
>   amendment did for 2 (`potenda/__init__.py:148-163`).
> - Apply compares the confirmed count with `report.left_out` before the first ownership proof,
>   so a refusal writes nothing.
>
> **Consequences.** Every plan saved before this release must be planned again before it is
> applied. The Dropdown choices change the consumed-schema fingerprint as well (ADR 0020), so
> such plans would be refused anyway. An older Sync release refuses a format-4 plan instead of
> applying it without the confirmation.
>
> **Alternatives considered.** An additive manifest key with no bump: rejected, because an
> older reader would ignore the confirmation. The report inside `operations.jsonl`: rejected,
> because it would change the bytes and the meaning of the file every consumer reads as
> operations.

## R8. Decision record 0020 (draft): records with problems are left out at the plan stage

Name: `dev/adr/0020-records-with-problems-are-left-out-of-a-plan.md`. It records the
engineering side of the AD-12 amendment (FR-029). The product amendment itself is a change to
`infrahub-sync-process/product/decisions.md`, outside this repository; its text is drafted
below for the product owner.

Draft content:

> **Status**: Proposed, needs product sign-off (spec 009 FR-029). **Date**: 2026-10-15.
>
> **Context.** A plan stops at the first bad source record (#369). Product decision AD-12 says
> failed dependencies are visible and block dependent work, and leaves the per-object policy
> open. `plan/derive.py` states that a derivation failure fails the command and that degrading to
> warn-and-drop would emit a silently incomplete plan (AD047, lines 33-37).
>
> **Decision.** At the plan stage, a record with one of six named problems, and every record
> that depends on it through a required relationship, is left out of the plan. The plan is not
> silently incomplete: it lists each problem, each left-out record and each record kept without
> an optional relationship, counts them in the summary, and its checksum covers that list
> (ADR 0022). Apply needs the person to confirm the left-out count. A `sync` run whose plan
> leaves out records stops after the plan. Every other plan failure, for example an unreachable
> system or a transform error, still fails the plan. During apply, an Infrahub rejection still
> stops the apply.
>
> **Consequences.** AD047's rule changes for these six problem types only. A record with a
> problem that already exists in Infrahub is not updated and not deleted. The policy for runs
> that nobody watches stays open for 3.0.
>
> **Alternatives considered.** `fail-fast` (today): one plan per problem. `continue` without a
> list: hides what was not written. `block` (leave out the whole kind): leaves out valid
> records the team could import.

Draft text for the AD-12 row in `decisions.md`:

> AD-12: Failed dependencies are visible and block dependent work. At the plan stage, records
> with problems and the records that depend on them through a required relationship are left
> out and counted; apply needs the person to confirm the left-out count, and a `sync` run whose
> plan leaves out records stops after the plan. The policy for runs that nobody watches is open
> for 3.0.

## R9. Decision record 0021 (draft): a run's failure record holds a redacted, bounded message

Name: `dev/adr/0021-a-run-failure-record-holds-a-redacted-message.md`. It replaces the rule in
`infrahub_sync/service/flow.py:723-727` ("A class name, never a message") and the
"category only" rule of `_operational_failure_summary` (`potenda/__init__.py:220-238`) and of
`_uniqueness_refusal_summary` (lines 104-145). It does not change ADR 0005: translation and
redaction still happen at the remote boundary only.

Draft content:

> **Status**: Proposed, needs product sign-off (spec 009 FR-030). **Date**: 2026-10-15.
>
> **Context.** A failed run records only an error class name, because a destination's text
> "can carry anything" (`service/flow.py:723-727`). A team then cannot tell why a run failed
> without the worker log (#369), and an Infrahub rejection is reported as "a destination GraphQL
> rejection" without Infrahub's code (#370). Infrahub 1.11 returns `extensions.code`, for
> example `ATTRIBUTE_CONSTRAINT_VIOLATION`, and `extensions.http_status` (#370).
>
> **Decision.** A run's failure record holds a structured cause: the stage, a failure type from
> a fixed list, the system involved, Infrahub's error code, the node kind and attribute when
> Infrahub returns them, and a message. The message is the text of the failure that decided the
> run, after three steps in this order: every collected secret and every value referenced
> through `$credential` is replaced, in raw and JSON-escaped forms (ADR 0001 encoding, spec
> FR-027, FR-028); control characters are removed; the result is cut to 1,024 characters. The
> same cause is printed by the CLI and returned by the Sync API. Source values may appear in the
> message, as they already appear in the plan operations the team reviews.
>
> **Consequences.** A destination's message reaches the run record. Text that is not a collected
> or referenced secret, for example a password that a source record holds as data, is not
> masked; this is the same exposure as the plan operations. The secret-redaction guideline and
> the comment at `service/flow.py:723-727` are rewritten to match. The CLI's `apply failed:
> <ClassName>` line (`cli.py:726-745`) becomes the cause.
>
> **Alternatives considered.** Keep class names and add a fixed message per error code: an
> unknown code would still say nothing. Return only the error code: #370 shows the attribute
> and value are what the team acts on.

## R10. How a failure is classified (FR-016 to FR-018, FR-025)

Decision: a new module `infrahub_sync/failure_cause.py` turns an exception into a
`RunFailureCause` (data-model.md). `_failure_evidence` (`service/flow.py:697-736`) calls it, so
the remote boundary stays the only place that translates (ADR 0005). The CLI's own handlers do
not change.

- The side and adapter come from a new `SideLoadError(ValueError)` that `_load_side`
  (`potenda/__init__.py:488-501`) raises instead of a bare `ValueError`. It subclasses
  `ValueError`, so ADR 0005's CLI handlers still match.
- `system_unreachable`: `requests.ConnectionError`, `httpx.ConnectError`, Infrahub SDK
  `ServerNotReachableError`, in the cause chain.
- `system_timeout`: `requests.Timeout`, `httpx.TimeoutException`, `ServerNotResponsiveError`.
- `token_rejected`: HTTP 401 or 403 from `requests`, `httpx` or `pynetbox.RequestError`, and
  Infrahub `AuthenticationError`.
- `tls_certificate_rejected`: `ssl.SSLCertVerificationError` in the cause chain (agreed with
  spec 013, whose worker adds a custom CA).
- `destination_rejected_write`: `GraphQLError` during apply. The error code, node kind and
  attribute are read from the first error's `extensions` when they are strings, bounded to
  128 characters each.
- `plan_problem_failure`, `verification_failed`, `apply_refused`, `credential_missing`,
  `internal_error` cover the rest; `internal_error` records only the class name and a fixed
  message, because its text comes from no known boundary.

Rationale: the cause chain is already walked for redaction (`sanitize_exception_chain`,
`execution.py:509`). Classifying by type in the chain needs no change to the adapters.

## R11. Credential values of any length and in JSON form (FR-027, FR-028)

Decision:

- `collect_secret_values` (`execution.py:411-456`) keeps the six-character floor for values it
  collects by name shape (`MIN_SECRET_LENGTH`, line 126), as FR-019 requires. A new
  `collect_referenced_credential_values(package, environ)` in `configuration/credentials.py`
  resolves every `$credential` reference and adds its value with no length floor. The worker
  merges both sets before the run starts.
- `publish_artifact` (`product_store/store.py:2925`) already redacts a JSON artifact's decoded
  strings, keys and numbers. For a JSON artifact, the raw byte pass after that step is removed,
  because the guideline records that it can corrupt JSON (for example `"234567,"`). The redacted
  document is parsed again before it is stored; if it does not parse, the publish fails and the
  run fails with `internal_error`, instead of storing an invalid plan.
- The run log, problem reasons, left-out and kept entries pass through the same `redact` with the
  merged set.

Risk: a one- or two-character credential replaces every occurrence of those characters in every
message. The spec requires masking whatever the length. The documentation recommends
credentials of at least six characters.

## R12. `configs validate` reaches the destination schema and the worker credentials

Decision for FR-032: `POST /configs/{config_id}/versions/{registry_version}/validate` accepts an
optional `branch` query parameter. With it, the route calls `configs.validate(...,
destination_schema=DestinationSchemaOptions(branch=…))` (`product_store/configs.py:863-910`),
which runs `collect_destination_schema_findings` (`configuration/schema_validation.py:183`). Today
the route never passes the option (`service/config_routes.py:146-175`). The check runs in
`sync-api`, which already holds `INFRAHUB_SYNC_CREDENTIAL_INFRAHUB_API_TOKEN`
(`docker-compose.yml:234`). The Dropdown check uses the choices of R3 against every literal
`static` value in the mapping. CLI: `infrahub-sync configs validate --branch <name>`.

Decision for FR-033: the worker writes the names of the `INFRAHUB_SYNC_CREDENTIAL_*` variables
that are set and not empty into a new product-store record, `worker_environment`, when it starts
and on each poll (`service/worker.py`). Names only, never values. Compose passes unset variables
as empty strings (`"${VAR:-}"`), so an empty value counts as unset. The validate route compares
the package's references with the most recent record of a live worker, and reports each missing
name as a finding `credential-not-set-in-worker`. When no live worker has a record, it reports
`worker-environment-unknown` as a warning.

Rationale: `sync-api` must not receive the source credentials (agreed with spec 013, least
privilege). Variable names are not secrets.

Alternatives considered: run validation as a worker run. Rejected: it would add an operation
that FR-025's stage list does not have, and the existing route is synchronous.

Decision for FR-034 (SYNC-147): the plan's create check (`refuse_unkeyed_create`,
`plan/keying.py:397`) and validation's check (`writable_convergence_reason` at
`configuration/schema_validation.py:251`) call one shared function for the part that depends on
the schema only. The value checks stay in the plan. A parametrized test runs both over the same
kinds and asserts the same answer.

Decision for FR-035 (SYNC-107): the responses of `configs register` and `configs version`
return the checksum without `declared_content`. `GET /configs/{id}/versions/{v}` keeps the
redacted content; whether it also needs this change is an open question for the product owner.

## R13. The NetBox example on the pinned `demo` dataset (FR-031, #374)

Decision, per case:

- Device status outside the five choices: changed mapping. A `device_status` transform in both
  `DcimDevice` entries (`examples/netbox_to_infrahub/package.yml:246-247`, `290-291`) translates
  `planned` and `staged` to `provisioning`, `offline` and `failed` to `maintenance`, `inventory`
  to `reserved`, and `decommissioning` to `deprecated`. This translation got the local copy
  through on 2026-10-05 (#374). The example README states the table.
- Rack position `2.5`: reported as a `wrong_value_type` problem. Rounding would write a
  position the device does not have.
- Two devices with the same name at two sites: reported as `destination_identity_collision`.
  The 2026-10-05 workaround used a hard-coded ID list and does not generalise (#374).

## R14. A stopped apply and the next plan (FR-020 to FR-022)

Decision:

- FR-020 is mostly in place: `ApplyRecord` (`plan/models.py:497-583`) records the applied
  operation IDs, the failing operation and whether it wrote. The cause adds the failing
  operation's kind and source identity, read from the plan by its ID.
- FR-021: updates are keyed by recorded destination IDs and creates converge by upsert
  (ADR 0012, ADR 0013), so a new plan against the destination as it now is contains only what
  does not match. A plan that follows a stopped apply of the same configuration reads the
  destination in full rather than incrementally, so that writes made after the previous
  cursor cannot be missed. Spec 010 owns the incremental-read rules; this rule is passed to it.
- FR-022: the product store already refuses any stage on a run that admitted a write
  (`_reservation_refused`, `product_store/store.py:1827-1857`). The refusal gets the error code
  `new-plan-required` and a message that names `infrahub-sync diff` as the next step.

## R15. Performance and size: evidence used

No tier M measurement was run (product owner constraint). Evidence: tier M is 10,207 objects,
first-sync plan step 4 min 32 s to 4 min 33 s, apply step 43 to 44 min (metrics.md); 66.5 MiB plan
at tier L (metrics.md); VAL-1 and VAL-18 (validation.md). The checks added here run in memory
over data already loaded. Whether they add measurable time is not known; spec 010's phase timing
will show it.
