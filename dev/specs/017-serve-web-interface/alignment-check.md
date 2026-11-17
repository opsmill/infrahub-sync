# Alignment check: specification against the request

**Date**: 2026-11-16

## Source

- Issue [#421](https://github.com/opsmill/infrahub-sync/issues/421), read with `gh issue view 421`
  on 2026-11-16: sections "Need", "Context" and "References".
- The inline request that started this specification: the maintainer's two decisions (browser
  token for the tab; a speckit specification), the default that the CLI adds `/api`, the
  orientation notes, the required records and the verification steps.

## Verdict

⚠️ MINOR DRIFT (proceeding).

## Findings

| Severity | Category | Source reference | Spec reference | Description |
|---|---|---|---|---|
| Minor | added | none | FR-034 | Security headers (Content-Security-Policy, framing) added by critique finding E3. Not in the issue or the request; needed because the page holds a token and starts applies. Kept. |
| Minor | added | none | FR-009 | Cache rules for the entry page and assets. A necessary detail of serving files so that an upgrade reaches the browser (US1 scenario 4). Kept. |
| Minor | added | request: "keep frontend changes minimal (token entry screen, bearer header, 401 handling, ...)" | FR-016, Assumptions | Two frontend changes beyond the listed ones: a "Remove token" item in the existing status menu (FR-016) and the plan and artifact downloads fetched with the header, because a link cannot send a bearer header. Both are needed for the token decision to work. Kept. |
| Minor | changed | issue Context: "The browser never holds the API token" | Decisions D-2 | The issue states today's design; the maintainer's decision D-2 replaces it. Recorded as a decision, not drift. |
| None | covered | issue Need: web interface at the root, Sync API under `/api`, no separate server | D-1, FR-001, FR-002, US1 | Covered. |
| None | covered | issue Context: every client of the routes (CLI, direct Prefect flow, scripts, health checks) | FR-021, FR-022, FR-023, US3 | Covered. Research R-11 found that the direct Prefect flow calls the execution module in process and builds no Sync API address, so it needs no change. |
| None | covered | issue Context: page paths equal to routes | Background, FR-005 | Covered. |
| None | covered | issue Context: verify, apply and cancel from the page | D-2, Assumptions (permissions unchanged), FR-034 | Covered. |
| None | covered | issue Context: submodule and Python-only Dockerfile | FR-024 to FR-027 | Covered. |
| None | covered | request: CLI adds `/api`, recorded as an assumption open to review | D-3 | Covered. |
| None | covered | request: unknown `/api` paths answer with the JSON envelope; start without a built web interface | FR-004, FR-029 | Covered. |
| None | covered | request: user docs, ADR without internal links, changelog fragment naming the address change | FR-031 to FR-033, tasks T033 to T035 | Covered. The plan adds two fragments (`added` and `changed`). |
| None | covered | request: verification (image build, `/runs/<id>` in a browser, CLI under `/api`, format, lint, ruff, unit tests) | quickstart, tasks T012, T036, T037 | Covered. |

## Action

Proceed. No remediation pass needed (0 of 2 used).
