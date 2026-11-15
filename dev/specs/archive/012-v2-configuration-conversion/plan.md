# Implementation Plan: Convert a v1 or v2 configuration and prove the first V3 plan

**Branch**: `012-v2-configuration-conversion` | **Date**: 2026-10-22 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `dev/specs/012-v2-configuration-conversion/spec.md`

## Summary

A team that runs Infrahub Sync v1 or v2 converts its `config.yml` into a V3 configuration package
with a local command, `infrahub-sync configs convert`. The command shows a report of every
change, asks once, and writes a package that registers without edits. A release gate proves on
the NetBox example that the first V3 plan against data v2 wrote holds nothing to create or
update.

Approach ([research.md](research.md)):

- A pure conversion module, `infrahub_sync/configuration/conversion.py`, takes the parsed v2
  mapping, the visible v2 environment variables (non-credential names only) and the two URL
  flags. It returns the package or the refusals, plus report entries. It does no I/O.
- It uses existing tables where they exist: credential settings and accepted settings from
  `BUILTIN_ADAPTER_CAPABILITIES`. It adds one fixed table of where v2 read the environment
  (research R2) and one constant of the Compose worker's credential variables (R5), each pinned
  by a test.
- Before writing, the package goes through `parse_configuration_package` and `collect_findings`,
  the checks registration runs without a connection (R6).
- `cli.py` gets a thin `configs convert` command: read, convert, print, confirm, write.
- A `v2-gate` suite in `.github/scripts/nightly_e2e.py` runs v2 `2.0.2`, converts, registers,
  plans, and compares the plan with an expected-delete list (R10).

## Technical Context

**Language/Version**: Python 3.10–3.13 (the CLI); the gate runs v2 `2.0.2` in its own `uvx`
environment.

**Primary Dependencies**: Typer (CLI), PyYAML (read and write), Pydantic (package parsing via
the existing models). No new dependency.

**Storage**: none. One output file per run; one `gate-result.json` per gate run.

**Testing**: pytest unit tests in `tests/configuration/` and `tests/cli/`; the gate is a nightly
suite, as the `from-netbox` check is.

**Target Platform**: wherever the V3 CLI is installed (Linux, macOS), and the Compose `cli`
service.

**Project Type**: CLI plus library module.

**Performance Goals**: converts a configuration as large as the NetBox example (about 730 lines)
in under 1 second; no network.

**Constraints**: no network, no Sync API, no credential value read or written; output is
deterministic for the same input and visible variables.

**Scale/Scope**: one file per call; 14 v2 examples to cover; 9 bundled adapters.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | How the design meets it |
| --- | --- | --- |
| I. Read-only by default | Pass | The command writes only a local file, after the report and a confirmation. It touches no source, no Infrahub and no Sync API. |
| II. Idempotency and safety | Pass | Same input gives the same package. No overwrite without `--force`; the write goes through a temporary file and a rename. |
| III. Adapter consistency | Pass | Credential names follow one pattern; credential and accepted settings come from `BUILTIN_ADAPTER_CAPABILITIES`. No adapter changes. |
| IV. Types and contracts | Pass | Typed dataclasses for the result and report entries; specific exceptions (`ConversionInputError`) instead of broad catches. |
| V. Test discipline | Pass | Parametrized unit tests per adapter row, per refusal and per example; the gate is an opt-in suite. |
| VI. Secrets | Pass | Only non-credential variable names are read; literal values never reach the report, errors or logs; YAML errors report line and column only; canary tests. |
| VII. Simplicity | Pass | One new module and one command; no new dependency; tables are constants pinned by tests. |

Post-design re-check: no change. No violation to justify.

## Project Structure

### Documentation (this feature)

```text
dev/specs/012-v2-configuration-conversion/
├── spec.md
├── plan.md                    # This file
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── convert-command.md
├── checklists/
│   └── requirements.md
└── tasks.md                   # /speckit-tasks output
```

### Source Code (repository root)

```text
infrahub_sync/
├── cli.py                                   # + `configs convert` command (thin)
└── configuration/
    └── conversion.py                        # new: pure conversion, tables, report entries

tests/
├── configuration/
│   ├── test_conversion.py                   # new: rules per key, per adapter row, refusals
│   ├── test_conversion_examples.py          # new: 14 v2 examples from the 2.0.2 tag (SC-002)
│   ├── test_conversion_secrets.py           # new: canary values (SC-003)
│   └── test_conversion_worker_variables.py  # new: constant equals docker-compose.yml sync-worker
├── cli/
│   └── test_convert_command.py              # new: confirmation, exit codes, output, --force
└── data/
    ├── v2_examples/                         # new: the 2.0.2 example config.yml files, copied
    └── v2_gate/
        └── expected_deletes.yml             # new: starts empty

.github/scripts/nightly_e2e.py               # + `v2-gate` suite
.github/workflows/workflow-nightly-e2e.yml   # + step that runs the suite
docs/docs/v2-to-v3.mdx                       # manual steps replaced by the command, gate result
docs/docs/configuration-package.mdx          # pointer to the command
docs/docs/reference/cli.mdx                  # regenerated
changelog/+v2-configuration-conversion.added.md
```

**Structure Decision**: the conversion sits in `infrahub_sync/configuration/` beside the package
models it produces, so `cli.py` keeps its role as a thin command layer. The v2 example files are
copied into `tests/data/v2_examples/` so unit tests do not depend on Git tags being fetched.

## Phases for tasks

1. **Conversion core**: tables (R2, R5), credential references, the environment-first rule,
   refusals, dropped keys, copying, the pre-write checks. Covers FR-006 to FR-021, FR-029.
2. **CLI command**: arguments, report printing, confirmation, exit codes, atomic write. Covers
   FR-001 to FR-005.
3. **Examples and secrets**: the 14-example test and the canary tests. Covers SC-002, SC-003.
4. **Gate**: `v2-gate` suite, expected-delete list, result file, nightly step. Covers FR-022 to
   FR-026 and SC-001. The left-out and problem checks wait for spec 009.
5. **Docs and changelog**: FR-027, FR-028.

## Dependencies and risks

- **Spec 009** (records left out and problems) is not on this branch. The gate cannot check
  FR-023 completely until it merges; the suite stops instead of passing (R10).
- **Compose worker variables**: three of the bundled adapters' credential variables reach the
  worker on this branch. The report warns for the others (FR-009); passing them is outside this
  plan.
- **Fixes for an unchanged second plan** (briefing D or other work): if the first V3 plan shows
  updates, each one is a defect to fix in V3 or in the conversion (FR-024). The amount of that
  work is not known until the gate runs.
- **v2 `2.0.2` from PyPI**: v1 and v2 shipped only on PyPI, so the gate installs it from there
  (R10). The gate needs network access to PyPI.
- **Docs layout**: the docs overhaul on `stack/v3-user-docs` moves the pages edited here (R11).

## Complexity Tracking

No constitution violation; nothing to justify.
