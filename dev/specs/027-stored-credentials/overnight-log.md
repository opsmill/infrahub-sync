# Overnight log: spec 027, night of 2026-11-20

Work area: branch `027-stored-credentials` in the worktree `.worktrees/027-stored-credentials`, created
from `feature/v3-develop` at `5c22b693`. Nothing is pushed and no pull request is opened. Advisor: the
`advisor` tool of the orchestrating session (Fable 5.2).

## Rules for the night (from the maintainer)

- Implement with `/speckit-opsmill-implement`, specification first.
- A critical blocker (deletes data, changes production, cannot be reversed, needs access only the
  maintainer has, or two readings with very different results) stops that item only. It goes in the
  "Blocked" section of the morning report.
- Every other question goes to the advisor with context, options and a recommendation. Each one is
  recorded below with the answer and what was done next.
- No push to `main`, no branch or data deletion, no messages to customers, no spending, no change to
  shared configuration.
- The same failure three times stops that item.

## Known stopping points

- **T074** (`emil-prototype`): the maintainer picks one version per screen. The prototypes are built,
  then T075 to T078 wait for the choice.
- **T080** (Helm pull request in `opsmill/infrahub-helm`): opened only on request.

## Advisor questions

(none yet)

## Progress

- 2026-11-20 evening: implementation started.
