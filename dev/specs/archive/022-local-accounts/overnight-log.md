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

### A2. Two Compose end-to-end failures that this branch did not cause (2026-11-18 evening)

Context: the first Compose and image run (image built from the worktree at 16:12 UTC, while the
alignment subagent was still editing documents; 104 passed, 5 failed) failed two Compose tests:

- `test_the_cli_container_holds_no_credential_beyond_its_own_api_token`: the `cli` container
  also holds `INFRAHUB_SYNC_WEB_DIRECTORY`, an `ENV` of the Dockerfile since spec 017
  (`b3416b38`, 2026-11-16);
- `test_the_deployed_worker_container_mounts_nothing`: the worker mounts the CA certificate
  volume read-only, added by spec 015 (`8080e42b`, 2026-11-14) as ADR 0026 decided.

Options: leave them failing and report; fix the assertions on this branch. Recommended: fix the
assertions, keeping equality checks.

Answer: fix them as stale assertions in a commit of their own, after checking that the base has
not run them green and that no other branch already fixed them; for the CLI test, check what the
test guards first. The three image failures (version and revision labels, source provenance)
come from the local build command, not from the code: rebuild from a clean, committed tree with
the build arguments before the final run.

What I did next:

- The nightly end-to-end workflow last ran on `feature/v3-develop` on 2026-10-02, before both
  changes, so these tests have not run since. Branches 021 and 024 have no change to either
  test.
- The CLI test guards the operator inputs that reach the `cli` container, by name. The image's
  own `ENV` is not an operator input and is not a credential, so the expected set now names it
  as an image default; the equality stays.
- The worker test now asserts exactly one mount: a volume named `..._sync-ca-certificates`,
  read-only, at `/etc/infrahub-sync/ca`; the test is renamed
  `test_the_deployed_worker_container_mounts_only_the_ca_directory`.
- Both tests rerun against the existing image; result in the events below.
- For the final image: `docker build --build-arg VERSION=<package version> --build-arg
  REVISION=$(git rev-parse HEAD) --build-arg CREATED=<UTC time>` from a clean tree (the Dockerfile
  declares the three arguments; the publishing workflow passes them as labels).

## Events

- Frontend submodule `frontend/vendor/infrahub` cloned (depth 1) in the worktree.
- First Compose and image run (`docker build -t infrahub-sync:compose-022 .`, then
  `pytest -m "compose or docker" tests/compose tests/image`, prefix `infrahub-sync-022`):
  16:12 to 16:28 UTC, 104 passed, 5 failed (see A2). All its containers are removed.
- The two corrected Compose tests passed against the same image:
  `test_the_deployed_worker_container_mounts_only_the_ca_directory` (16:33:46 UTC, 103 s) and
  `test_the_cli_container_holds_no_credential_beyond_its_own_api_token` (16:33:59 UTC).
- User Story 2 (T047 to T054) done: nine commits `63fd2da3` to `945b219f`; the headless browser
  scenario passed 16 steps (Playwright 1.65.2, server on 127.0.0.1:18761, stopped). Not tested
  in the browser: "approved by", the busy message and the idle timeout. Unit tier: 9922 passed,
  0 failed, 2 xfailed (one fewer because a two-case test of `tasks/dev.py` became one case).
- User Story 4 (T062 to T066) started.
- User Story 4 (T062 to T066) done: nine commits `bb1f41e8` to `ceec7cae`; the browser scenario
  passed 49 steps. All 74 tasks of `tasks.md` are ticked.
- Review of the web interface (read-only): no critical or high finding; four medium and three
  low findings sent to a fix subagent.
- Second full Compose and image run, image built from the clean commit `ceec7cae` with
  `VERSION=3.0.0a5`, `REVISION` and `CREATED` build arguments: 17:22 to 17:38 UTC, 108 passed,
  1 failed. The failure is `test_the_image_carries_the_source_provenance_of_the_commit_it_was_built_from`:
  the label held `ceec7cae` (correct), but the fix subagent committed `3452441a` during the run,
  so `git rev-parse HEAD` no longer matched. A last run from the final commit, with no commit
  during it, follows the review fixes.
- Review fixes of the web interface: five commits `09232682` to `f7bc00f7`; all seven findings
  fixed; the browser scenario passed 69 of 69 steps (49 earlier steps and 20 new ones). Left:
  if an account is deleted while its tokens dialog is open, the old token rows may stay.
- The fragment `changelog/+web-interface-no-actor.changed.md` was rewritten, not deleted: it now
  says only that the configuration list does not name who started the latest run.
- Final run on the final commit `91e00376`, tree clean, no commit during the run:
  - image built with `VERSION=3.0.0a5`, `REVISION=91e00376…` and `CREATED`;
  - Compose and image tiers (`pytest -m "compose or docker" tests/compose tests/image`, prefix
    `infrahub-sync-022`): 17:55 to 18:11 UTC, 109 passed, 0 failed; no container left;
  - `invoke lint` and `ruff check .`: clean; unit tier: 9922 passed, 4 skipped, 2 xfailed,
    0 failed; `tests.check-310`: all three legs passed (7028 passed); `pnpm check` and
    `pnpm typecheck`: pass; the five CLI `--help` commands: exit 0.

## Morning report

### 1. Done

- **All 74 tasks of `tasks.md` are ticked**, User Stories 1 to 4. The web interface asks for
  an account name and a password, names the account that started a run, approved a plan or
  registered a version, and has "My account" and "Accounts" pages. The token field is gone.
- **Design documents match the code**: research, data model, the four contracts, plan,
  quickstart, five spec requirements (marked "Updated 2026-11-18 to match the
  implementation"), the user documentation and ADR 0049.
- **Tests**: unit tier 9922 passed, 0 failed; Python 3.10 legs pass; Compose and image tiers
  109 passed on the final commit; headless browser scenarios: 16 steps (User Story 2), 49
  steps (User Story 4) and 69 steps after the review fixes, all passed.
- **Branch**: `022-local-accounts`, 88 commits ahead of `origin/feature/v3-develop`, not
  pushed, no pull request, no `Claude-Session` trailer. Report of the implementation:
  `dev/specs/022-local-accounts/opsmill-implement-report.md`; screenshots and scripts of the
  browser checks are in the session scratchpad (`shot-*.png`, `us4-*.png`,
  `review-phone-header-*.png`, `web_review.py`).

### 2. Blocked

Nothing is blocked.

### 3. Advisor decisions

See "Advisor questions" above: A1 (six questions of the spec alignment, and the checks for the
web interface) and A2 (two stale Compose assertions, fixed; the image tier needs the build
arguments).

### 4. Not done

- **Pull request**: not opened, by the standing rule that a pull request needs your explicit
  request.
- **Browser checks not covered**: the `503 sign-in-busy` message, the idle timeout of a
  session, "Plan approved by" (the fake orchestration makes no plan), and the message shown when
  the browser refuses to copy a token.
- **Deferred review findings** of the API and CLI, listed in section 5 of
  `opsmill-implement-report.md` (type invariants, simplifications, an unreadable session file
  treated as empty).
- **Spec archive**: `speckit-opsmill-extract` has not run.

### 5. Risks to check before accepting

- **Choices made without you** in the web interface: no API token field at all (only sign-in);
  the storage key renamed to `infrahub-sync.session-token`, so an open tab that held a token
  shows the sign-in form; an administrator's own row offers no level change and no password
  change (the page points to "My account"); deleting an account requires typing its name;
  `admin` offers only unlock.
- **Known limit of the sign-in refusal**: with about four or more `sync-api` processes, an
  attacker can fill the 10,000-row table with refusals in force, after which a new name's
  failures are not counted (research R6). Alternative: refuse uncounted names while the table
  is full.
- **Rewritten changelog fragment** `changelog/+web-interface-no-actor.changed.md` (from spec
  017): it now keeps only the claim that the configuration list does not name who started the
  latest run.
- **Two Compose tests changed** to accept the CA mount of the worker and the web directory of
  the image (A2). The nightly end-to-end workflow had not run on `feature/v3-develop` since
  2026-10-02.
- **Generated API types**: the 422 descriptions changed from "Unprocessable Content" to
  "Unprocessable Entity", probably from the FastAPI version installed here.
- **`infrahub_sync/service/account_actions.py` is at Pylint's 1,000-line limit**: the next
  change there must split it.
- **Helm chart PR #129** (infrahub-helm): its end-to-end job will fail until it runs an
  Infrahub Sync image with local accounts; chart version and `appVersion` not raised.
