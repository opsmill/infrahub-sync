# Extraction Record

**Extracted on**: 2026-11-14
**Extracted by**: speckit.opsmill.extract

## ADRs Created

- `dev/adr/0031-an-import-writes-as-the-account-of-its-token.md` (from R-3, R-4, R-6, the
  Clarifications and decisions D-3, D-4, D-8)

## Knowledge Updated

- `docs/docs/development/knowledge/planned-write-and-apply.md` (new subsection "Source, owner
  and protection of a write", under "Applying one operation"; from R-3, R-4, R-6)
- `docs/docs/development/knowledge/incremental-and-cache.md`, section "Cursor safety and current
  reachability": why a plan that reads who last changed each value reads the Infrahub
  destination in full (from R-2)
- `dev/adr/README.md` and `docs/docs/development/adr-index.mdx` (ADR index)

## Guidelines Updated

None. The opt-in integration test and its permissions are already in
`docs/docs/development/guidelines/testing-tiers.md`, and the text of a 403 answer that a failure
record may hold is already in `docs/docs/development/guidelines/secret-redaction.md`.

## Not extracted

- R-1 (the metadata read needs Infrahub 1.7.0; the plan continues without it otherwise):
  already in `docs/docs/adapters/infrahub.mdx` ("Who last changed a value") and
  `docs/docs/development/knowledge/plan-artifact.md`.
- R-2 measurements (what Infrahub 1.10.6 and 1.11.9 return, and the read times): they describe
  one machine and two Infrahub versions, and stay in `research.md`.
- R-3 and decision D-7 (a node named as `{kind, name}`, the allowed kinds, the stop with no
  fallback): already in `docs/docs/adapters/infrahub.mdx` ("The import account", "Name a source
  or an owner").
- R-4 (the third plan file and the manifest fields `lineage` and `last_changes`): already in
  `plan-artifact.md`, ADR 0001 and ADR 0022.
- R-5 (a missing permission and a refused token): already in ADR 0021,
  `docs/docs/development/knowledge/repository-tour.md` and `docs/docs/adapters/infrahub.mdx`.
- R-6, what a person who uses the import views (owner, protection, values written before,
  resource-pool allocations): already in `docs/docs/adapters/infrahub.mdx`.
- R-7 (the permissions of the import account): already in `docs/docs/adapters/infrahub.mdx`
  ("Permissions of the import account"), with the correction of 2026-11-02.
- `contracts/`: already in `docs/docs/adapters/infrahub.mdx`,
  `docs/docs/reference/durable-product-records.mdx`, `plan-artifact.md`,
  `docs/docs/reference/sync-http-api.mdx` and `docs/docs/running-a-sync.mdx`.
- `data-model.md`, `plan.md`, `quickstart.md`, `tasks.md`, `opsmill-implement-report.md` and
  `checklists/requirements.md`: execution records.
- `spec.md`, "Points Passed to the 3.0 Design": open product questions, not decisions.

## Open at archive time

- Task T004, not tracked. The task asks for the time a plan takes to read about 10,000 nodes
  from Infrahub, measured with the NetBox benchmark runner (`tasks/bench.py`). The read time
  was measured with and without the last-change metadata, on `main` and on another branch, on
  Infrahub 1.10.6 and on 1.11.9, but with a direct SDK read and not with the runner. The numbers
  are in `research.md` (R-2) and in `opsmill-implement-report.md`. The runner has no option to
  turn the metadata read off, and it records the time of a whole stage, not the time of the
  destination read. R-2 says the sources set no threshold for this time.
- An update sends every source attribute, not only the fields the plan changes, so an unchanged
  value also gets the import's source, not tracked. The product owner decided on 2026-10-23 that
  an update should send only the changed fields. This is not implemented
  (`docs/docs/development/knowledge/plan-artifact.md`, "The last-change file").
- The constitution asks for `structlog`, and the code uses the standard `logging` module, not
  tracked. Whether to change the code or the constitution is not decided.
- A draft bug report for the Infrahub project exists outside this repository, not tracked. On
  Infrahub 1.10.6 and 1.11.9, after a rename on `main`, a lookup by the earlier name still finds
  the node on a branch created before the rename. Whether to file the report is not decided.
- In the NetBox run on Infrahub 1.11.9, Infrahub recorded 274 nodes of `IpamPrefix`,
  `IpamIPAddress` and `IpamAggregate` as created by `__system__`, although their attributes have
  the import's source, not tracked. The cause is not known.
- Review findings deferred as later changes to the code, not tracked: the same shapes are
  defined in the client, plan and adapter types; `kind: str` is used where a `Literal` type
  exists; `infrahub_sync/potenda/__init__.py` is not split.

Statements of `research.md` that the code no longer matches:

- R-3 says the adapter calls `client.get_user()`. The adapter sends its own `AccountProfile`
  query (`IMPORT_ACCOUNT_QUERY` in `infrahub_sync/adapters/infrahub.py`), because `get_user()`
  returns only the display label.
- R-4 says `PLAN_FORMAT_VERSION` does not change. That was true for this spec. Spec 020 later
  raised it to 4 (ADR 0022).
- R-7 lists a permission set that is not enough for real kinds. The corrected set is in
  `docs/docs/adapters/infrahub.mdx` ("Permissions of the import account").

## Archive

The spec directory moved to `dev/specs/archive/014-import-lineage/` as a historical record.
