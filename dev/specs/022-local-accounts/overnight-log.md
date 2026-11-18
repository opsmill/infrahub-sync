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

None yet.

## Events

- Frontend submodule `frontend/vendor/infrahub` cloned (depth 1) in the worktree.
