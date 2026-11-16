# Decision records

MADR-format decision records. Name each file `nnnn-title.md`: a zero-padded 4-digit
sequence and a lowercase kebab-case title, no `adr-` prefix, for example `0001-use-uv.md`.

## Current records

- [0001 — Saved plan artifact format](0001-saved-plan-artifact-format.md) — the manifest and
  canonical-JSON operations a run records, the commit point, the checksum, derived operation
  identifiers, and recursive peer references.
- [0002 — The destination planned-write surface is a Protocol](0002-planned-write-destination-protocol.md)
  — the typed boundary a saved-plan apply writes through, and the member-presence-only limit of
  the runtime check.
- [0003 — The replace-set flush is a targeted relationship write](0003-replace-set-flush-is-a-targeted-relationship-write.md)
  — **superseded by 0012** — why no whole-node re-render can flush a reconciled peer set, and the
  live test that pins the destination's replace semantics.
- [0004 — Deletes are recorded but never executed](0004-deletes-are-recorded-but-never-executed.md)
  — the delete contract and the rule that a plan may only record what it can know.
- [0005 — Translate run failures only at the remote boundary](0005-translate-run-failures-only-at-the-remote-boundary.md)
  — how the shared execution surface gains typed sanitized failures without changing CLI
  behaviour.
- [0006 — Resolve remote configurations file by file](0006-resolve-remote-configurations-file-by-file.md)
  — why the remote surface walks the configuration directory tolerantly instead of reusing
  the eager CLI lookup.
- [0007 — A canonical plan fingerprint is the equivalence oracle between run paths](0007-canonical-plan-fingerprint-as-equivalence-oracle.md)
  — how "the remote path produced the same plan" becomes a testable claim.
- [0008 — Declare Redis directly instead of using the diffsync extra](0008-declare-redis-directly-instead-of-the-diffsync-extra.md)
  — the dependency conflict that forced a base declaration change, and why the floor is
  permissive.
- [0009 — Optional integrations live in their own package and are proven absent in CI](0009-optional-integrations-live-in-their-own-package.md)
  — the import boundary for an optional integration and the three mechanisms that enforce it.
- [0010 — A managed write holds one configuration guard and ends in a verdict, not a retry](0010-a-managed-write-ends-in-a-verdict-not-a-retry.md)
  — **partly superseded by 0027** — how a configuration's writes are serialized, why the
  ownership boundary is required rather than optional, and why an uncertain write is terminal
  evidence instead of a retry.
- [0011 — Internal run bundles are private, uncompressed, and never redacted](0011-internal-run-bundles-are-private-and-byte-stable.md)
  — why stage handoff state is a second artifact class, why its bytes are byte-stable and
  exempt from redaction, and how an untrusted archive is bounded and validated.
- [0012 — The convergent upsert is the replace-set write](0012-the-convergent-upsert-is-the-replace-set-write.md)
  — why the targeted relationship flush was deleted, what the live shrink test now pins on the
  Upsert mutation, and the escalation that is unchanged. Supersedes 0003.
- [0013 — Writes are keyed by a recorded id and a complete human-friendly ID](0013-writes-are-keyed-by-recorded-id-and-complete-hfid.md)
  — why the private-render gate was retired, how an update is keyed by an id recorded at plan
  time and a create by proving its own payload, and what plan format 3 carries. Closes AD067.
- [0014 — V3 writes through saved-plan apply](0014-v3-writes-through-saved-plan-apply.md)
  — why the direct DiffSync write path is removed from v3.
- [0015 — Images publish to Harbor through one reusable workflow](0015-images-publish-to-harbor-through-one-reusable-workflow.md)
  — why the artifact handoff became a Harbor build-and-push workflow, how a tag waits for every
  platform's smoke test, and when `latest` moves.
- [0016 — The Compose deployment is one tag-pinned file](0016-the-compose-deployment-is-one-tag-pinned-file.md)
  — why the wrapper and digest binding became a root `docker-compose.yml` pinned by the
  release commit, and what was given up.
- [0019 — Prefect is an internal component of Sync](0019-prefect-is-an-internal-component-of-sync.md)
  — why Sync runs its own Prefect server and PostgreSQL server instead of Infrahub's task
  manager and `task-manager-db`, and what that costs.
- [0020 — Records with problems are left out of a plan, counted, and confirmed at apply](0020-records-with-problems-are-left-out-of-a-plan.md)
  — why a plan finds every problem in one pass, leaves out the records with a
  problem and the records that depend on them, keeps records through optional relationships,
  withholds the deletes of a kind it cannot match, and asks for a confirmation at apply.
- [0021 — A run's failure record holds a structured cause built from allowlisted fields](0021-a-failure-record-holds-a-structured-cause.md)
  — why a failed run records a failure type, the system involved and
  Infrahub's own message of a rejected write, and never the text of any other error.
- [0022 — Saved plan format 4 holds the plan report](0022-saved-plan-format-4.md)
  — the report file and the manifest's `report` record, how the plan checksum
  binds the left-out count the apply checks, and why formats 2 and 3 can no longer be applied.
- [0024 — Prefect shows a Sync stage's outcome only after the run record stores it](0024-prefect-surfaces-follow-the-stored-run-record.md)
  — why a stage's last log line and run summary artifact are written after the outcome is
  stored, where their outcome and next action come from, what the artifact may contain, and
  why a recorded verdict is the reported state.
- [0025 — A warm source read admits its changed records and reads a kind in full after a problem](0025-a-warm-source-read-admits-changed-records.md)
  — why the source snapshot stores the source record ID and a completeness
  marker, and why a kind with a problem is read in full, so a warm read reports the problems
  of a full read.
- [0026 — The Compose deployment takes an external object store, adapter credentials and CA certificates from `.env`](0026-the-compose-deployment-takes-an-external-store-credentials-and-ca-from-env.md)
  — why the bundled store is scaled to zero to leave it out, why the worker receives a fixed
  list of credential variables, and why the CA directory is the one read-only shared mount.
- [0027 — An interrupted apply resumes from its saved position](0027-an-interrupted-apply-resumes-from-its-saved-position.md)
  — why an apply saves a progress marker, why `verify` is admitted on an interrupted run and
  reports each uncertain operation, and why a resume on the same run clears
  `reconciliation_required`. Partly supersedes 0010.
- [0028 — The bundled object store runs from the Silo image](0028-the-bundled-object-store-runs-from-the-silo-image.md)
  — why every Sync Compose stack runs the object store from a pinned `pgsty/silo` release, the
  standard variant, and why the volume repair step stays.
- [0029 — The development stack is one Compose file with profiles, run by one `dev` namespace](0029-the-development-stack-is-one-compose-file-with-profiles.md)
  — why the preview stack was merged into one file with `sync` and `infrahub` profiles, why the
  tasks read settings from the resolved Compose file, and when the image rebuilds.
- [0030 — The end-to-end test class inherits Infrahub's, and the Prefect pins move together](0030-the-end-to-end-test-class-inherits-infrahubs.md)
  — why `TestInfrahubSyncDocker` inherits `TestInfrahubDocker`, and why the `prefect` and
  `prefect-client` pins must name one version.
- [0031 — An import writes as the account of its token, and sets an owner only on values it creates](0031-an-import-writes-as-the-account-of-its-token.md)
  — why the import account comes from the API token, why a missing named source or owner stops
  the run, why an apply refuses a plan made with another account, and why an update sets the
  owner only on the relationship edges it adds.
- [0032 — The run, configuration and version lists page with a cursor and read only the product store](0032-run-and-configuration-lists-page-with-a-cursor.md)
  — why the three lists page by position in a fixed order and not by offset, why a cursor is
  valid only for its list and filters, and why every value a list shows or filters on is stored
  in a column.
- [0033 — An event is saved in the same transaction as the decision it records](0033-an-event-is-saved-in-the-transaction-of-its-decision.md)
  — why the event of an accepted request or a stage result is saved by the store call that saves
  the answer or the result, which events are saved by a call of their own, and how events are
  typed, related to resources and paged.
- [0034 — A run's log is read from Prefect through the Sync API and holds only the lines that Sync wrote](0034-a-run-log-is-read-from-prefect-through-the-sync-api.md)
  — why Sync keeps no copy of a run's log, how a line that Sync wrote is told from a line of
  Prefect's engine, and why a recognized failure ends the flow run with a returned `Failed` state.
- [0035 — The engine reports each side's read to an optional observer, and the Sync API derives the read state it returns](0035-the-engine-reports-each-sides-read-to-an-optional-observer.md)
  — why `Potenda._load_side` reports a read to an observer that only the worker sets, why each
  side has its own column on the execution's row, and why the returned state is derived when the
  run is read.
- [0036 — `verify` runs every pre-write check of `apply` through one shared gate](0036-verify-runs-every-pre-write-check-of-apply-through-one-shared-gate.md)
  — why `verify` contacts the destination, why `apply` and `verify` call `Potenda.check_plan`,
  and which named checks a `verify` reports.
- [0037 — An approval is a record on the run that the Sync API stores, and one service setting makes it required](0037-an-approval-is-a-record-on-the-run.md)
  — why an approval is four columns of the run and not a phase, why the Sync API answers
  `approve` without a flow run, and what `INFRAHUB_SYNC_REQUIRE_APPROVAL` refuses.
- [0038 — Cancelling a finished plan withdraws it, and a cancelled run is never applied](0038-cancelling-a-finished-plan-withdraws-it.md)
  — why `runs cancel` withdraws a plan that has nothing running, and why `apply`, `approve` and
  `resume` refuse a run in phase `cancelled`.
- [0039 — Workers publish the names of their credential variables so validation can check them](0039-workers-publish-the-names-of-their-credential-variables.md)
  — why each worker writes the names of its credential variables to the product store, which
  workers count as live, and what grows in the `worker_environments` table.
- [0040 — A v2 configuration converts to a package locally, by v2's own rules](0040-a-v2-configuration-converts-locally-by-v2-rules.md)
  — why `configs convert` is a local command, why fixed tables reproduce v2's environment
  variables and authentication methods, and why it never reads a credential value.
- [0041 — An IP namespace that a uniqueness constraint needs and the file does not map converts to the `default` namespace](0041-an-unmapped-ip-namespace-converts-to-the-default-namespace.md)
  — why `--live-schema` reads the destination schema and maps an unmapped IP namespace to
  `default` as a peer that exists only in the destination.
- [0042 — The v2 gate passes on no creates and no updates, and on reviewed lists for everything else](0042-the-v2-gate-passes-on-no-creates-and-reviewed-lists.md)
  — why the nightly gate pins v2 2.0.1, what makes it pass, and how its lists of records left
  out and deletes are kept.
- [0043 — The Kubernetes deployment orders its start with a hashed Job and gates](0043-the-kubernetes-deployment-orders-its-start-with-a-hashed-job-and-gates.md)
  — why the Helm chart runs the bootstrap as a plain Job named after a hash of its inputs, why the
  Sync API and the worker wait for the Prefect deployment, and why an upgrade can start them
  before the new Job completes.

## Related

- [Knowledge](https://github.com/opsmill/infrahub-sync/blob/feature/v3-develop/docs/docs/development/knowledge/index.md) — how the system works after these decisions.
- [Guidelines](https://github.com/opsmill/infrahub-sync/blob/feature/v3-develop/docs/docs/development/guidelines/index.md) — the rules they imply for new code.
- [Constitution](https://github.com/opsmill/infrahub-sync/blob/feature/v3-develop/docs/docs/development/constitution.md) — the principles they serve.
