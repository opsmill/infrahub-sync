# Extraction Record

**Extracted on**: 2026-11-18
**Extracted by**: speckit.opsmill.extract

## ADRs Amended

No ADR was created. The two decision records that this spec wrote cover every decision of
`research.md` that affects the structure of the system: the account tables, scrypt and its
limits, API tokens and session tokens stored as SHA-256 digests, the session token kept in
`sessionStorage` with no cookie, the refusal after failed sign-ins and its bounded table, the
`admin` account of the environment, account identifiers in records, and the deletion of an
account. They were amended:

- `dev/adr/0049-sign-in-uses-local-accounts-and-the-environment-defines-admin.md`:
    - Decision, "No cookie": the web interface signs in with a password and keeps the session
      token under the key `infrahub-sync.session-token`; it has no field for an API token. The
      earlier text said this sign-in was not delivered yet (from R9, User Story 2).
    - Decision, new item: disabling an account deletes its tokens and its sessions, and a new
      session or API token is inserted only while the account is active, with the account row
      locked (from R9).
    - Decision, new item: an accepted account action and its audit event are written in one
      transaction, and a refusal event is written on its own (from R10).
    - Source line: the archive path, with R10 added.
- `dev/adr/0050-a-record-holds-an-account-identifier-and-a-deletion-empties-the-account-row.md`:
  source line changed to the archive path.

## Knowledge Updated

- `docs/docs/development/knowledge/local-accounts.md`, new page: where the code is, the four
  tables, how a request is resolved, passwords and the limits on password checks, sign-in and
  the refusal after failed sign-ins, the `admin` account, account names in answers, free text
  and token values, audit events, the session file of the CLI, and the web interface (from R1
  to R10, `data-model.md`, and the code).
- `docs/docs/development/knowledge/index.md`: new section "Accounts and sign-in".
- `docs/docs/development/knowledge/repository-tour.md`: a link from the list of account modules
  to the new page. The modules themselves were already listed by the implementation.
- `docs/sidebars.ts`: the new knowledge page and the new guidelines page.

## Guidelines Updated

- `docs/docs/development/guidelines/accounts.md`, new page: record the account identifier,
  never the name; return an identifier only in a field that `AccountNameMiddleware` presents,
  with a route case in the test of every response model; write an account action and its audit
  event in one transaction, and lock the account row when a credential is created; refuse a
  change to the `admin` account (from R1, R4, R5, R7, R9, R10).
- `docs/docs/development/guidelines/secret-redaction.md`, new section "Check every free-text
  field of a request for token values" (from R4, R5).
- `docs/docs/development/guidelines/index.md`: entry for the new page.

The rule for naming accounts in the web interface was already in `AGENTS.md`, section "Sync
frontend", and was not repeated.

## Other files

- `dev/specs/archive/README.md`: a section that says `022-local-accounts` is not spec 016,
  whose number before the renumbering of 2026-11-15 was 022.
- The relative links in `spec.md`, `research.md`, `plan.md` and `tasks.md` were changed for the
  new depth of the directory. Spec 017 is not archived, so its link points to
  `../../017-serve-web-interface/`.
- Text in `plan.md`, `tasks.md`, `opsmill-implement-report.md` and `overnight-log.md` that names
  `dev/specs/022-local-accounts` was left as written: these files record the work as it ran.

## Not extracted

- Routes, request and answer fields, error codes and CLI commands (`contracts/http-api.md`,
  `contracts/cli.md`): already in `docs/docs/reference/sync-http-api.mdx` and
  `docs/docs/reference/cli.mdx`.
- The steps an operator follows (`quickstart.md`, `contracts/environment.md`): already in
  `docs/docs/guides/accounts.mdx`, `docs/docs/compose-deployment.mdx`,
  `docs/docs/kubernetes-deployment.mdx` and `docs/docs/development-stack.mdx`.
- `contracts/web-interface.md`: the behavior is in the code of `frontend/src/` and summarized on
  the new knowledge page; the naming rule is in `AGENTS.md`.
- R8 (the pattern and the reserved account names): in `ACCOUNT_NAME_PATTERN` and
  `RESERVED_ACCOUNT_NAMES` of `infrahub_sync/service/account_actions.py`, and in
  `docs/docs/guides/accounts.mdx`.
- R11 (removal of `INFRAHUB_SYNC_SERVICE_BEARER_TOKENS`, test helpers, the development stack
  and the Helm chart values): a one-time change; the result is in the deployment pages and in
  `tests/service/accounts.py`.
- R12: lists the decision records and the `AGENTS.md` changes that the implementation already
  made, and the amendment of constitution principle VI, already in
  `docs/docs/development/constitution.md`.
- `plan.md`, `tasks.md`, `notes.md`, `checklists/`, `critiques/`, `alignment-check.md`,
  `opsmill-implement-report.md` and `overnight-log.md`: execution records.

## Open at archive time

All 74 tasks of `tasks.md` are ticked, User Stories 1 to 4 included. Section 3 of
`opsmill-implement-report.md` says User Stories 2 and 4 were not started; it was written before
they were built, and `overnight-log.md` ("Morning report") records their completion.

- Known limit of the refusal after failed sign-ins, kept by the maintainer on 2026-11-18, not
  tracked. With about four or more `sync-api` processes, failed sign-ins for unknown names can
  fill the 10,000-row table with refusals in force; a failed sign-in for a name that has no row
  is then not counted (R6). The alternative recorded in R6 is to refuse every sign-in for a name
  that is not counted while the table is full.
- Known limit of the refusal rule, not tracked. A person who knows an account name can keep that
  account from signing in with its password by failing 5 times in each 15-minute window (R6).
  The alternative, a delay that grows with each failure, is recorded in the critique.
- Implementation report, section 7, item 1: confirm decision D-10 and the rule of FR-021. Whether
  the maintainer confirmed them is not known.
- Merge conflicts with other branches, not tracked (implementation report, section 7, item 4,
  and `notes.md`, section "T074"). Spec 021 (scheduled runs) adds `principal_of` to the resolver
  that this branch removes, and a schedule must hold an account identifier.
- Helm chart pull request #129 in `opsmill/infrahub-helm`: its end-to-end job fails until it
  runs an Infrahub Sync image with local accounts. The chart version and `appVersion` were not
  raised (`overnight-log.md`, "Risks to check before accepting").
- `infrahub_sync/service/account_actions.py` is at the 1,000-line limit of Pylint; the next
  change there must split the file.
- Browser checks not run, not tracked: the `503 sign-in-busy` message, the idle timeout of a
  session, "Plan approved by", and the message shown when the browser refuses to copy a token.
- Choices made in the web interface without the maintainer, to confirm (`overnight-log.md`,
  "Risks to check before accepting"): no API token field; the storage key renamed to
  `infrahub-sync.session-token`, so a tab that held a token shows the sign-in form; an
  administrator's own row offers no level change and no password change; deleting an account
  requires typing its name; `admin` offers only unlock.
- Deferred review findings, not tracked (implementation report, section 5):
    - a session file that cannot be read is treated as empty (`cli_session.py`);
    - an identifier with no account is shown as stored (`presentation.py`);
    - server request models hide secrets in `__repr__` only, not with `Field(repr=False)`;
    - type invariants not enforced at construction (`Account`, `ResolvedCredential`,
      `Principal`, `AdminAccountSettings`);
    - a raw traceback for a store error at start (`serve.py`); `login` leaves an open session
      when it cannot save it; the hint after a failed file change;
    - eight simplifications, such as a shared helper for token deletion;
    - tests: distinct placeholders for two deletions, boundary cases of expiry and idle time,
      and route tests on SQLite only.
- The generated API types changed the description of `422` answers from "Unprocessable Content"
  to "Unprocessable Entity", probably because of the installed FastAPI version. Not checked.

## Archive

The spec directory moved to `dev/specs/archive/022-local-accounts/` as a historical record.
