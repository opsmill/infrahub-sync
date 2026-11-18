# Overnight log: spec 022, night of 2026-11-18

Work area: branch `022-local-accounts` in the worktree `.worktrees/022-local-accounts`. Nothing
is pushed and no pull request is opened. Advisor: the `advisor` tool of the orchestrating session.

## Plan

1. Align the spec documents, the user documentation and the comments with the code.
2. Implement User Story 2 (sign-in in the web interface), tasks T047 to T054.
3. Implement User Story 4 (accounts and tokens in the web interface), tasks T062 to T066.
4. Run the Compose end-to-end tier once session 024 has torn down its development stack.
5. Review the new work and write the morning report.

## Coordination with other sessions

- Session 024 (`infrahub-sync-2f`) runs the development stack on ports 8000, 8001 and 8082
  until about 45 minutes after 2026-11-18 evening; it will send a message when it ends. This
  session promised not to use Docker until then.
- Session 021 (`infrahub-sync-76`) uses `sync021-pg` (127.0.0.1:55432) and an isolated stack,
  project `infrahub-sync-021`, ports 18000, 18001, 14230, 15440 and 19010. This session's
  Compose tier uses prefix `infrahub-sync-022`, ports 8061, 4261 and 8081, and its own image
  tag.

## Advisor questions

### A1. Six questions raised by the alignment of the spec documents (2026-11-18 evening)

Context: the subagent that aligned the documents with the code found six points the code does
not settle. Each was sent with the options and a recommendation.

1. **A full table of refusals leaves a failed sign-in for a new name uncounted.**
   - Recommended: refuse uncounted names while the table is full.
   - Answer: keep the behavior; the maintainer confirmed the 15-minute rule and has not seen a
     rule that refuses names with no failures. Check whether the state is reachable, record it
     as a known limit in research R6 and the spec's assumptions, and list it under risks with
     the alternative.
   - Next: done by the follow-up commit (see events). Calculation: 10,000 names × 5 failures =
     50,000 checks, which must land inside one 15-minute refusal; one process runs about 12 to
     20 checks a second (2 at a time, 0.1 to 0.16 s each), so about 11,000 to 18,000 checks in 15
     minutes: not reachable with one process, reachable with about three or more.
2. **A deleted account's name stays in the CLI session file on a workstation.**
   - Recommended and answer: outside FR-030 (the service cannot reach that file). Add it to R7's
     list of places a deleted name can remain, and to the accounts guide.
3. **A sign-in with a name no account can have writes no refusal record.**
   - Recommended and answer: keep (a test pins it); state the exception in R6, R10,
     `contracts/http-api.md` and the HTTP reference.
4. **The CLI has no fixed message for `forbidden`, `unauthenticated`, `request-invalid` and
   `secret-parameter-refused`.**
   - Answer: compare an existing command with an account command for the four codes; add a fixed
     message only where the account command prints less. Low priority.
5. **Should the "Accounts" page offer unlock for `admin`?**
   - Recommended and answer: yes, unlock only, shown when `sign_in_refused_until` is in the
     future. Add unlock to the User Story 4 action list of `contracts/web-interface.md` (it was
     missing for every account, although T064 lists it). Settle before the User Story 4 brief.
6. **The `account_routes.py` docstring says the administrator check runs before the body is
   read, but JSON that cannot be parsed is refused first.**
   - Recommended and answer: fix the text, not the behavior; check that `POST /runs` also refuses
     unparseable JSON before authentication, and reword the "403 on every route" line of
     `contracts/http-api.md`.

Extra points from the same answer, for User Stories 2 and 4:

- Rebuild the image and rerun the web-serving and image tests after User Story 4, because the
  Compose run started this evening predates the web interface change.
- T054 and T066 need a browser scenario: script a headless browser from the scratchpad, without
  adding a dependency to `frontend/package.json`; if that is impossible, leave both unticked and
  report them under risks.
- Generate the API types from this branch's own OpenAPI document written to a file, not from
  `127.0.0.1:8001`, which may be another session's API.
- User Story 2 rewrites the passages that describe web sign-in with an account token, and adds a
  changelog fragment.
- Check whether the Vite development proxy injects an `Authorization` header.
- Replace the "colleague's agreement" precondition with the maintainer's instruction of
  2026-11-18 in `tasks.md`, the spec's assumptions and `contracts/web-interface.md`.
- `infrahub_sync/service/account_actions.py` is at Pylint's 1,000-line limit: split it first if
  an API change is needed.

## Events

- Frontend submodule `frontend/vendor/infrahub` cloned (depth 1) in the worktree.
