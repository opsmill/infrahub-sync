# V3 release scope: MVP, 3.0 and 3.x

## Summary

- **Problem.** Customers already run Sync v1 or v2, and V3 adds services: the Sync API, a worker, a Prefect server, PostgreSQL and object storage. In a first import from a real NetBox:
  - V3 stops at the first bad source record.
  - A failed run does not say why.
  - A plan for a full import cannot be reviewed.
  - A plan on unchanged data takes about 20 minutes at 10,207 objects.
- **What is needed.**
  - **The MVP** is the release that customers test with OpsMill. It lets them:
    - review the plan
    - find bad source data before anything is written
    - apply exactly what they reviewed
    - view the run history, in a basic UI
  - **3.0** lets a v2 customer replace v2 in production, and adds deletes limited to the objects a configuration created.
- **Not decided yet.**
  - whether a different S3 server replaces the bundled MinIO, in 3.0 or later (AD-3)
  - whether Infrahub's `is_protected` blocks updates from a second configuration
  - product sign-off on five changes to earlier decisions: the UI in the MVP (AD-5), records
    left out at the plan stage (AD-12), failure messages in run records (#369), deletes in 3.0,
    and the new definition of 3.0
  - how the 3.0 delete rules treat objects that a converted v2 configuration wrote with its
    `owner`

Benoit Kohler set this scope on 2026-10-15. It replaces the definition in
[path-to-3.0.md](https://github.com/opsmill/infrahub-sync-process/blob/main/product/path-to-3.0.md),
where 3.0 is complete when every card linked to
[INFP-628](https://opsmill.atlassian.net/browse/INFP-628) is done.

## How to tell the releases apart

- **MVP (the testing release).** A customer team that runs v1 or v2 completes a first import from
  their own NetBox into an Infrahub branch, on one Docker Compose host, with OpsMill support.
  Up to tier M (10,207 mapped objects). Every requirement in specifications 009 to 013 is MVP.
- **3.0.** A v2 customer can replace v2 in production and lose nothing. No specification
  exists yet.
- **3.x.** New capabilities after 3.0, in any order. No specification exists yet.

## MVP specifications

| Specification | What the customer team can do | Requirements from the scope decision |
| --- | --- | --- |
| [009 Plan problem reporting](009-plan-problem-reporting/spec.md) | View every bad source record in one plan, apply the rest after confirming the count left out, view why a run failed, plan again after a stopped apply, check a configuration against the destination schema before a run | Plan collects every problem; records with problems are left out; failure causes shown; recovery by planning again; credential masking; NetBox example on real data; `configs validate` checks |
| [010 Second sync and plan speed](010-second-sync-plan-speed/spec.md) | Plan again after a change and view only that change, at tier M in no more time than the first-sync plan | Second sync shows only real changes; plan time at tier M |
| [011 Operator UI](011-operator-ui/spec.md) | Review, approve and apply a plan, and view runs, in a browser on the `sync-api` host; list and cancel runs in the CLI | UI served by `sync-api`; `GET /runs`; plan detail in pages; each peer identity shown once; `runs list` and `runs cancel` |
| [012 v2 configuration conversion](012-v2-configuration-conversion/spec.md) | Convert a v2 `config.yml`, and view a first V3 plan with no false changes on data v2 wrote | Conversion command; no-false-updates check |
| [013 Compose images](013-compose-images/spec.md) | Deploy with fewer images, store run files in their own S3-compatible store or the bundled MinIO, pass any bundled adapter's credentials and a custom CA to the worker, and pull a released image by digest | Prefect server from the Sync image as its own service, port not published; current MinIO build kept, pinned by digest; external S3-compatible store; worker credentials and CA; reliable lifecycle tests; image digest and SBOM |

Suggested order: 009, 010, 011, 012, 013. Specification 011 uses the API that 009 defines.

### MVP blockers assigned to specifications

[path-to-3.0.md](https://github.com/opsmill/infrahub-sync-process/blob/main/product/path-to-3.0.md)
names these problems for the MVP cards. Each one now has an owner:

- Short credentials and masking that corrupts JSON (SYNC-82, SYNC-262): specification 009,
  FR-027 and FR-028, by keeping credentials out of causes, problem reasons and plan files;
  removing the existing masking pass on published plans is a follow-up after the MVP.
- The NetBox example package fails on data that real NetBox instances hold
  ([#374](https://github.com/opsmill/infrahub-sync/issues/374)): specification 009, FR-031,
  because its success criteria run on that example.
- `configs validate` cannot reach the destination schema check or the credential check, and
  validation and planning can disagree (INFP-651, INFP-652, SYNC-107, SYNC-147): specification
  009, FR-032 to FR-035, because these are problems found before a plan.
- Each peer's identity is printed twice in plan detail
  ([#373](https://github.com/opsmill/infrahub-sync/issues/373), SYNC-259): specification 011,
  FR-039.
- The CLI cannot list or cancel runs (SYNC-211, SYNC-254): specification 011, FR-040 and FR-041.
- Compose passes credentials for only three adapters to the worker, and has no custom CA
  (INFP-692): specification 013, FR-023 to FR-025.
- Compose can store plans and run files only in its bundled MinIO, not in an S3-compatible
  store the customer already runs (product owner decision of 2026-10-15): specification 013,
  FR-028 to FR-034.
- The Compose lifecycle tests fail at random, and the image digest and SBOM are not tied to a
  release (INFP-692): specification 013, FR-026 and FR-027.

### MVP work that is not a specification

These decision records need amendments:

- [ADR 0019](../adr/0019-prefect-is-an-internal-component-of-sync.md): which image runs the
  Prefect server (specification 013).
- AD-12 in
  [decisions.md](https://github.com/opsmill/infrahub-sync-process/blob/main/product/decisions.md):
  at the plan stage, records with problems are left out and counted (specification 009, FR-029).
- AD-5 in the same file: the UI moves into the MVP. AD-5 said "built after recovery and
  operations" (specification 011, FR-037).
- The rule that a run's failure record holds a class name and never a message
  ([#369](https://github.com/opsmill/infrahub-sync/issues/369);
  [`flow.py`](../../infrahub_sync/service/flow.py), lines 722-725). Specification 009 shows
  Infrahub's message and the cause fields, built only from allowlisted fields, and FR-030
  requires a decision record.

Each of these changes needs product sign-off before the work that depends on it ships.

### Not in the MVP

- tier L, and the 64 MiB plan checkpoint and 16 MiB client response limits
- deletes executed (they are shown in the plan, as today)
- schedules
- custom adapters
- resuming a stopped apply
- an option that applies a plan with records left out without review
- opening the UI from other computers over TLS (a person on another computer uses a tunnel they
  set up themselves)
- Kubernetes
- editing configurations in the UI
- running Prefect inside the `sync-api` container (not planned in any release)

## 3.0: replace v2 in production

No specification exists yet for these items. The JPD card is named where one exists, with its
status on 2026-10-06 according to
[path-to-3.0.md](https://github.com/opsmill/infrahub-sync-process/blob/main/product/path-to-3.0.md).

- **Upgrades:**
  - upgrade without a reset
  - backup and restore
  - part of [INFP-663](https://opsmill.atlassian.net/browse/INFP-663), IN PLANNING
- **Custom adapters** through the adapter contract:
  [INFP-654](https://opsmill.atlassian.net/browse/INFP-654), IN PLANNING.
- **Schedules** with control of overlapping runs:
  [INFP-656](https://opsmill.atlassian.net/browse/INFP-656), IN PLANNING.
- **Recovery:**
  - crash-safe or resumable apply
  - the AD-12 policy for runs that nobody watches, including any option that applies a plan
    with records left out without a person confirming the count (the MVP stops a `sync` run
    after such a plan, specification 009 FR-012)
  - [INFP-650](https://opsmill.atlassian.net/browse/INFP-650), IN PLANNING
- **Tier L** with the 64 MiB and 16 MiB limits removed:
  [INFP-659](https://opsmill.atlassian.net/browse/INFP-659), IN PLANNING.
  [metrics.md](https://github.com/opsmill/infrahub-sync-process/blob/main/product/metrics.md)
  records the tier L results.
- **Branch review** with a Proposed Change:
  [INFP-655](https://opsmill.atlassian.net/browse/INFP-655), IN PLANNING.
- **Health checks and documentation** for Compose: the rest of INFP-663, and
  [INFP-665](https://opsmill.atlassian.net/browse/INFP-665), In Development.
- **Access to the UI and the Sync API from other computers over TLS**, through a documented
  reverse proxy with a certificate the customer supplies: part of INFP-663. In the MVP, the UI
  is reachable from the `sync-api` host only (specification 011 FR-031).
- **Deletes**, under the rules below. Card:
  [INFP-660](https://opsmill.atlassian.net/browse/INFP-660), IN PLANNING.
  - v2 never deletes from Infrahub, according to
    [deletes.md](https://github.com/opsmill/infrahub-sync-process/blob/main/product/design/deletes.md),
    so this is new behaviour, not v2 parity.
  - On 2026-09-19 the product owner kept deletes recorded only. Moving them into 3.0 needs product sign-off.

### Delete rules for 3.0

1. A configuration may delete an object only if the object's Infrahub `owner` is that
   configuration's account. `owner` is set when the object is created and is not changed by
   updates. For example, a device created by a NetBox configuration and later updated by a LibreNMS
   configuration can still be deleted by the NetBox configuration. `source` records the last
   writer of each value and plays no part in deletes.
2. Relationships of kind `Component` default to `on_delete: cascade` in Infrahub
   ([`schema_branch.py`](https://github.com/opsmill/infrahub/blob/stable/backend/infrahub/core/schema/schema_branch.py),
   `process_relationships`). For each proposed delete, the plan reads the `owner` of every object
   Infrahub would delete with it. If any of them has another owner, that delete is refused.
3. A configuration with deletes enabled needs its own `owner` account. Registration refuses a
   missing account, or one that another configuration uses.
4. A plan proposes deletes for a kind only after a complete read of that kind. A threshold
   counts the objects deleted along with each delete.
5. Deletes are approved separately from creates and updates. Apply checks the set of objects
   again before each delete.
6. Deletes are off by default.
7. Objects without an owner are never deleted. This covers objects created by hand and objects
   v2 wrote without lineage.

The Infrahub adapter sets `source`, `owner` and `is_protected=True` on every create and update today
([`adapters/infrahub.py`](../../infrahub_sync/adapters/infrahub.py), in `apply_planned_operation`).
Rule 1 requires updates to stop sending `owner`.

Open point for the 3.0 delete design: specification 012 FR-017 keeps the v2 `source` and
`owner` settings when it converts a v2 configuration, so V3 writes carry the same lineage as v2
writes. Specification 012 also states that v2 sets `owner` on its writes. Under rule 1, a
converted configuration could then delete objects that v2 wrote with that `owner`. Rule 7
covers only objects that v2 wrote without lineage. The 3.0 delete design decides whether
objects that v2 wrote with an `owner` can be deleted by the converted configuration. The MVP does not change, because deletes are not executed in the MVP.

## 3.x: after 3.0

The cards are listed with their status on 2026-10-06:

- incremental extraction: [INFP-657](https://opsmill.atlassian.net/browse/INFP-657), IN PLANNING
- event triggers: [INFP-658](https://opsmill.atlassian.net/browse/INFP-658), IN PLANNING
- parallel partitions: [INFP-661](https://opsmill.atlassian.net/browse/INFP-661), IN PLANNING
- alternative planner: [INFP-662](https://opsmill.atlassian.net/browse/INFP-662), On Hold
- Kubernetes: part of INFP-663
- a fuller UI with configuration editing: [INFP-664](https://opsmill.atlassian.net/browse/INFP-664)
- the Python library: [INFP-646](https://opsmill.atlassian.net/browse/INFP-646), marked Duplicate

## Questions answered in the MVP specifications

| Question | Specification | Answer |
| --- | --- | --- |
| Q1. Is a record that refers to a left-out record only through an optional relationship also left out? | 009, FR-005 | No. Keep it without that relationship, and list it in the plan |
| Q2. Does `sync` (plan and apply in one request) stop when its plan leaves out records? | 009, FR-012 | Yes. Stop after the plan, so a person confirms the count before a write. The MVP has no option that skips that stop; the policy for runs that nobody watches, including any such option, is 3.0 |
| Q3. Who views the time each plan phase takes? | 010, FR-013 | The customer team, in the run record, marked as a field that may change |
| Q4. Can the team cancel a run from the UI? | 011, FR-024 | Yes, through the existing `POST /runs/{run_id}/cancel` route |
| Q5. Who can reach the UI? | 011, FR-031 | The `sync-api` host only, on loopback; a person on another computer uses a tunnel they set up. Access from other computers over TLS through a reverse proxy is 3.0 (INFP-663) |
| Q6. What happens to a v2 setting that a registered package does not accept? | 012, FR-010 | Refuse the conversion and list every such setting |
| Q7. If the new MinIO image cannot read the old volume, may operators reset the object store? | 013, FR-019 | No longer applies: on 2026-10-15 the product owner kept the current MinIO build, so the update keeps the data with no reset |

Benoit Kohler answered these questions on 2026-10-15.

## Other open questions

- **AD-3.** AD-3 keeps run files in S3-compatible storage, based on bundles of about 3 MB for
  88,000 objects (VAL-18). A tier L plan measured 66.5 MiB. On 2026-10-15 the product owner kept
  the current MinIO build for the MVP, with no replacement S3 server, and required Sync to work
  with a customer's own S3-compatible store. Whether a different S3 server, a shared volume or
  PostgreSQL replaces the bundled MinIO, in 3.0 or later, will be decided later.
- **`is_protected`.** Does `is_protected=True` stop a second configuration from updating a value
  that another configuration owns? This affects the 3.0 delete rules.
- **Product sign-off** on five changes to earlier decisions:
  - the UI in the MVP (AD-5, specification 011 FR-037)
  - records left out at the plan stage (AD-12, specification 009 FR-029)
  - failure messages in run records instead of class names only (#369, specification 009
    FR-030)
  - deletes in 3.0 (the 2026-09-19 decision)
  - the new definition of 3.0
- **Owner of objects that v2 wrote.** See the open point under "Delete rules for 3.0".
