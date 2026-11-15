# Extraction Record

**Extracted on**: 2026-11-14
**Extracted by**: speckit.opsmill.extract

## ADRs Created

- `dev/adr/0040-a-v2-configuration-converts-locally-by-v2-rules.md` (from R1 to R4, R6 to R9, and
  FR-006, FR-019, FR-020 and FR-030)
- `dev/adr/0041-an-unmapped-ip-namespace-converts-to-the-default-namespace.md` (from FR-002,
  FR-031 and `opsmill-implement-report.md`, sections 8 and 9)
- `dev/adr/0042-the-v2-gate-passes-on-no-creates-and-reviewed-lists.md` (from FR-022 to FR-026
  and `opsmill-implement-report.md`, sections 8, 10, 11 and 12). It records that v2 `2.0.1` stays
  the pinned version of the gate, by the product owner's decision of 2026-11-13, and how the two
  reviewed lists are maintained.

## Knowledge Updated

- `docs/docs/development/knowledge/v2-configuration-conversion.md`: new page on how
  `configs convert` is built (from R1 to R9 and `data-model.md`). It is listed in
  `docs/docs/development/knowledge/index.md` and `docs/sidebars.ts`.
- `docs/docs/development/knowledge/repository-tour.md`:
    - section "The command-line client and its HTTP client": `configs convert` does not call the
      Sync API, and the list of commands is complete;
    - section "Configuration admission": the four `conversion` modules, and the schema read of
      `--live-schema`;
    - section "Tests": the fixture directories `tests/data/v2_examples/` and
      `tests/data/v2_gate/`.
- `docs/docs/development/knowledge/schema-mapping.md`:
    - section "Fields": a `static` value with a `reference` names a peer that exists only in the
      destination (from `opsmill-implement-report.md`, section 9);
    - section "`identifiers` is not the convergence key": the paragraph on `IpamPrefix` and
      `IpamIPAddress` described identifiers that the NetBox example no longer has.
- `dev/adr/README.md` and `docs/docs/development/adr-index.mdx` (ADR index)
- `docs/docs/compose-deployment.mdx`, section "Use it": the page said "The CLI reaches the Sync
  API and nothing else". With `configs convert --live-schema` the CLI reads the schema of the
  destination Infrahub. The sentence now says what the `cli` service is given, and names the
  exception.

## Guidelines Updated

- `docs/docs/development/guidelines/secret-redaction.md`, section "Never chain a validation
  library's raw detail": a parse error reports only the position (from R9)
- `docs/docs/development/guidelines/testing-tiers.md`, section "The `v2-gate` suite": which
  records go on the reviewed lists, and when to review the lists (from FR-023 and
  `opsmill-implement-report.md`, section 10)
- `docs/docs/development/guidelines/testing.md`, section "Pin a code constant to the deployment
  file it mirrors" (from R5): written with the extraction of spec 015, which changed the
  constant the example names. Nothing was added to it here; the new knowledge page links to it.

## Changed from the extraction plan

Each statement was read in the code on 2026-11-14 before it was written. These statements of
the plan were narrowed:

- The plan said that each row of the fixed tables has a unit test. The pages say that
  `tests/configuration/test_conversion.py` holds each row of `V2_ENVIRONMENT_FIRST` a second
  time. The tests of the two other tables were not read row by row.
- The plan said that the tables record what released v2 did. The code says the tables are
  frozen at the `2.0.2` tag, and the pages say that.
- The plan said that a failure of the gate fails the nightly job and blocks the testing
  release. Only the first part is in the code. ADR 0042 says that no release workflow reads the
  result of the nightly job.
- The plan said that the token reference is removed before the schema read. The code removes
  the `token` setting of the destination from a copy of the package.
- The plan said that the index entries go after the entry of ADR 0027. They go after the entry
  of ADR 0039, the last one.
- The plan proposed extraction comments in the changed pages. None was added: the extractions
  of specs 014 and 007 added none.

## Not extracted

- How the command reads addresses and other settings from the environment, the order of the
  variables, the `${NAME}` placeholder rule and the `*_env_vars` settings (R2, R3, FR-010 to
  FR-013, FR-029, FR-030): already in `docs/docs/v2-to-v3.mdx`.
- Which credential settings get a reference, and the `auth_method` table (R4): already in
  `docs/docs/v2-to-v3.mdx`.
- How each v2 key is copied, dropped or refused (FR-015 to FR-019): already in
  `docs/docs/v2-to-v3.mdx` and `docs/docs/configuration-package.mdx`.
- What `--live-schema` changes, and the two cases where it does not (FR-031): already in
  `docs/docs/v2-to-v3.mdx`.
- Exit statuses, options, warnings and refusals (R8, FR-003 to FR-005, FR-028,
  `contracts/convert-command.md`): already in `docs/docs/reference/cli.mdx` and
  `docs/docs/v2-to-v3.mdx`.
- The steps of the gate, its pass conditions and `gate-result.json` (FR-022 to FR-026): already
  in `docs/docs/development/guidelines/testing-tiers.md` and `docs/docs/v2-to-v3.mdx`.
- Why the worker receives a fixed list of credential variables (R5): already ADR 0026.
- How to copy the files of `tests/data/v2_examples/` again: already in
  `tests/data/v2_examples/README.md`.
- `data-model.md`: the result and report types are in
  `infrahub_sync/configuration/conversion.py`, and the formats of the two lists are in the
  header comments of `tests/data/v2_gate/expected_left_out.yml` and `expected_deletes.yml`.
- R11, `plan.md`, `quickstart.md`, `quickstart-results.md`, `alignment-check.md`, `checklists/`
  and `critiques/`: execution records.
- `opsmill-implement-report.md`, sections 1 to 6: the record of the October run.

## Where the spec files and the merged code differ

The code, FR-019, FR-023, FR-031 and sections 7 to 12 of `opsmill-implement-report.md` are
right in each case:

- The header of `opsmill-implement-report.md` says "INCOMPLETE. 38 of 40 tasks are done".
  Sections 7 to 12 replace it: section 12 says that all 40 tasks are done. Read the report from
  section 7.
- R4, first decision, gave every declared credential setting a reference. The correction in R4
  replaces it.
- R5 says the worker receives three credential variables. The worker receives 24, listed in
  `COMPOSE_WORKER_CREDENTIAL_SUFFIXES` in `infrahub_sync/configuration/credentials.py`.
- R10 says the gate installs v2 `2.0.2` from PyPI, uses the preview stack, runs `diff` and
  counts no records left out. The gate installs `2.0.1`, runs on the development stack, converts
  with `--live-schema`, runs `plan`, and compares records left out and problems with a reviewed
  list.
- FR-022 says v2 `2.0.2` and a conversion with `--yes` only. The gate runs `2.0.1` and converts
  with `--live-schema --yes`.
- SC-001 says v2 `2.0.2` and "0 records left out, 0 problems". FR-023 replaced this.
- `data-model.md` describes only the list of expected deletes, and has no section on
  `expected_left_out.yml`.

Two more facts from section 7 of the report:

- The October branch `012-v2-configuration-conversion` was deleted. Its 22 commits were moved
  onto `feature/v3-develop` on 2026-11-13, and the conversion was then adapted to the lineage
  rule of spec 009: a v2 group name becomes `{kind: CoreAccountGroup, name}`.
- Decided on 2026-11-13: the conversion does not set `protect`, because `protect` is not the V3
  default.

## Open at archive time

`tasks.md` has no open task: all 40 tasks are ticked.

Findings that `opsmill-implement-report.md`, section 4, marks as deferred or kept, none
tracked:

- Expected-delete identities are compared without the display escaping of the CLI (low,
  deferred), not tracked. Still present on 2026-11-14: `identity_text` in
  `.github/scripts/nightly_e2e.py` does not escape. The report gave as a reason that the list
  was empty. The list now holds one entry, `IpamNamespace name=default`, which holds no character
  that the CLI escapes.
- The refusal text names the internal key INFP-654 (low, deferred), not tracked. Still present
  on 2026-11-14 in `infrahub_sync/configuration/conversion.py`.
- When the same adapter is the source and the destination, both sides share one credential
  reference (medium), not tracked. The report adds a note and the naming was kept (FR-006). Not
  checked again.

Items of `opsmill-implement-report.md`, section 5 ("Autonomous decisions to revisit"), that no
later section settles, none tracked:

- The gate reads the value v2 wrote through the Infrahub SDK by destination ID, not by
  human-friendly ID. Still present on 2026-11-14.
- Prometheus: an unknown `auth_method` is refused, although v2 sent no credential in that case,
  and `basic` needs both a user name and a password, although v2 accepted an empty password. Not
  checked again.
- Slurp'it: only `api_key` gets a reference. Still present on 2026-11-14
  (`V2_FIXED_CREDENTIALS`).
- `tests/test_workflow_contracts.py` failed 6 tests in one run and passed in the next two; the
  cause was not found. The file passed on 2026-11-14.

Open questions found during the extraction, none tracked:

- FR-026 requires that a failure of the gate blocks the testing release. The gate fails the
  nightly job, and no release workflow reads the result of the nightly job. How the release is
  blocked is not known.
- The comment above the `cli` service in the root `docker-compose.yml` still says that the
  service "reaches the Sync API and nothing else". It was not changed, because the packaged
  copy must stay byte-equal and the change is outside this extraction.
- The gate depends on PyPI keeping `infrahub-sync==2.0.1`. There is no plan to move to `2.0.2`
  (product owner's decision of 2026-11-13).

## Archive

The spec directory moved to `dev/specs/archive/006-v2-configuration-conversion/` as a
historical record.
