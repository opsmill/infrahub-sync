# Overnight log: 024-demo-ready-netbox

Run rules (from the maintainer, 2026-11-18 evening):

- Unattended. Critical blockers go to "Blocked" in the morning report; everything else goes to the
  advisor (the `advisor` tool of this session), and the answer is followed.
- Limits assumed (the placeholders were not filled): no push, no pull request, no deleted branch or
  data, no change to shared configuration, no message outside this machine. Only exception:
  replies to another local Claude session (spec 022) about Docker use.
- Work only on branch `024-demo-ready-netbox`, worktree `.worktrees/024-demo-ready-netbox`.
- The same failure three times stops that item.

## Timeline

- Prep done before the run: spec, plan, critique (3 must-address fixed), tasks (29), alignment
  ⚠️ minor drift. Constitution check added to plan.md at the start of implementation.
- Chunk 1 (T001–T010, `demo prepare`): ✅ commit `23232abf`. Flags carried to the review phase:
  - a second run with another `--reason` gets 409 `idempotency-conflict` (the reason is part of the
    request fingerprint, not of the key);
  - `schema_updated` may be false on a fresh Infrahub's first load, which would report `unchanged`;
  - the contract and quickstart say `--url`; the CLI option is `--api-url`.
- Chunk 2 (T011–T016, `dev.demo`): dispatched. A local session working on spec 022 waits for a
  one-line message when this chunk's Docker stack is destroyed.

- Chunk 2 (T011–T016): ✅ T011–T015, commits `cbce50c7`, `4d59a0f1`. ⚠️ T016 live scenarios not
  run: stopped stacks `infrahub-sync-dev` and `infrahub-sync-netbox` (not ours) exist, and NetBox's
  ready step attaches to the fixed `infrahub-sync-dev` worker; `dev.destroy` would delete their
  data. Another session runs `infrahub-sync-021`. Chunk 2 rebuilt the shared `infrahub-sync:dev`
  tag through `dev.build`. Told the spec 022 session that Docker is free from our side.

- Chunk 3 (T017–T022): ✅ T017–T021, commit `d168c641` (ADR 0048). Image test passed on its own
  tag `infrahub-sync:demo-024-test`, removed afterwards. ⚠️ T022 live part deferred. Flag for review:
  `build_inputs_hash` in `tasks/dev.py` does not hash the two asset paths.
- Chunk 4 (T023–T026): ✅ commits `ec4fb79c`, `a7a3b44b`. New page
  `docs/docs/tutorials/try-the-netbox-demo.mdx`; publishing guide gained "A pre-release image of
  the V3 line". Flags: `main` has no `ci-docker-image.yml`, so a dispatch uses
  `--ref feature/v3-develop` (an earlier dispatch from a non-main branch worked); Vale exceptions
  added for `Kubernetes` and `Styrmin`; web interface steps written from the frontend source, not run.
- Order change (mine): review runs before chunk 5 (T027–T029), so the final verification in T028
  covers the review fixes.
- Review (4 agents in parallel: code, errors, tests, comments+types; simplify deferred until after
  fixes). No critical finding. Converging findings: `--reason` 409 on re-run (all four), schema
  non-convergence only warns then registers, replayed 201 reports `versioned`, URL userinfo not
  redacted, missing tests for FR-010 package canary and Sync API errors at the CLI. Refuted:
  first schema load reports `unchanged` (a fresh Infrahub already has a core schema hash), token
  leaks, Python 3.10 issues. One fix subagent dispatched for items 1–9.
- The spec 022 session runs its Compose tier as `infrahub-sync-022`, ports 8061, 4261, 8081.

## Advisor questions

1. **Q:** the live scenarios (quickstart 1–3) would touch stacks that are not ours. Options: skip and
   report; run with an overridden project name; delete the old stacks. Recommended: try an override,
   else skip.
   **A:** never touch or destroy `infrahub-sync-dev`/`infrahub-sync-netbox` (group 1). Finish the
   offline work first; at the end, try once with an isolated project name, own ports and own image
   tag, only if every step (including NetBox's attach and the teardown) honours the override and the
   021/022 sessions confirm they are done; otherwise skip and mark T016/T022 not done. Add to the
   review: replayed version call state, `--reason` in the key hash, read the SDK `schema_updated`
   before fixing, run `tests.check-310`. Chunk 3 must not run `dev.build`.
   **Next:** dispatched chunk 3 with those limits. At the end, checked the override: the NetBox
   tasks attach the fixed `infrahub-sync-dev` worker (`tasks/netbox.py:472`, `:747`), so the check
   failed and the live scenarios were skipped, as the answer requires.

2. **Q:** is the run complete? **A:** not yet: write the morning report in the requested five
   sections; check that `ci-docker-image.yml` can be dispatched from `feature/v3-develop`; rerun
   `docs.generate`, `docs.docusaurus` and `tests.check-310` on the final tree; try one live run of
   `demo prepare` by hand on an isolated Compose project (no NetBox, no invoke task) if sessions 021
   and 022 agree, else skip. **Next:** `gh` shows `ci-docker-image.yml` is not registered from
   `main`, but a `workflow_dispatch` run from branch `007-harbor-image-publishing` succeeded on
   2026-10-01, so a dispatch with `--ref` works; ADR 0048 collides with no other worktree (022 uses
   0049 and 0050); `in_sync()` is called as the SDK's own `wait_until_converged` calls it. Both
   sessions agreed to a stack on project `infrahub-sync-024`, ports 28000, 28001, 24230, 25440,
   29010, image `infrahub-sync:demo-024`; started it.

- Fix subagent: items 1–8 ✅, item 9 ⚠️ finished by the orchestrator (stale `--url`, key and state
  wording in plan.md, research.md, data-model.md). Commits `4207356b`, `cff006e6`.
- Chunk 5 done by the orchestrator: news fragment, `/.dev` ignored in `.yamllint.yml`, full
  verification (unit tier 9003 passed at 17:11:55Z), no `Claude-Session` trailer, no token-like
  string in the diff, no change in `../styrmin` or `../infrahub-helm-sync-chart`. Commit `1e5d9ffd`.
- Report written: `opsmill-implement-report.md`, status INCOMPLETE (T016, T022 live parts).
- Live run on project `infrahub-sync-024` (2026-11-19, no NetBox, no invoke task). `demo prepare`
  from the host, four runs between 13:59:10Z and 13:59:38Z: `loaded`/`registered` v1; another
  reason, `unchanged` v1; `http://netbox:8080`, `versioned` v2; back to the first address,
  `unchanged` v1. From the image `infrahub-sync:demo-024` at 13:59:44Z, with no checkout:
  `versioned` v3, 16 schema files. Then `down --volumes` and the image removed; no container or
  image of that project is left.
- Final checks on the final tree (2026-11-19): `docs.generate` changed no file; `docs.docusaurus`
  built the site; `tests.check-310` passed all three legs (6648 passed, 101 skipped, 2 xfailed).
- Morning report written: `morning-report.md`.

## Follow-up on 2026-11-19 ("finish the missing steps where you do not need me")

3. **Q:** may I change the NetBox tasks so that they attach the worker of the stack's own project,
   then run `dev.demo` on isolated projects? **A:** yes: pass the project down instead of resolving
   it in `tasks/netbox.py`; check every setting read-only first; snapshot the other projects and
   compare after teardown; tell sessions 021 and 022 about the NetBox port; do not apply.
   **Next:** commit `e7921073`. Session 021 agreed; session 022 is no longer running. The
   `infrahub-sync-dev` containers had been removed by someone else; their volumes remain and were
   not touched.

- Scenario 1: `dev.demo` in 2 min 30 s (14:16:08Z to 14:18:38Z, Docker cache warm), then a plan of
  1,687 creates across 20 kinds by 14:19:07Z. No apply (AGENTS.md requires human approval).
- Scenario 2: re-run in 7 s, `unchanged` and `unchanged`.
- Scenario 3: from the image, `versioned` v2, no token in the output.
- The smoke schema does not conflict with the destination schema; scenario 4 now uses a node
  `ClusterGeneric`, and `demo prepare` stopped with exit 1, named the conflict, and registered nothing.
- **Start run** in the web interface plans the latest version (frontend source); the tutorial now
  says how to plan an earlier version.
- Teardown: both projects, the image and `netbox.local.env` removed; the other projects' containers,
  networks and the volume list are identical to the snapshot.

## Rebase onto `feature/v3-develop` (2026-11-19)

- Rebased onto `1dc5e204` (schedules, #437; local accounts, #436). Commit IDs in these reports are
  the new ones.
- Conflicts: `infrahub_sync/cli.py` (the `demo` and `schedules` command groups, and the client
  error handler, which now also removes a refused saved session), `dev/adr/README.md` (0048 kept
  beside 0049 to 0053), `docs/docs/reference/cli.mdx` (regenerated).
- The local accounts change removed `BEARER_TOKENS` from the development stack and replaced the
  web interface's token prompt with an account name and a password. `dev.demo` read the removed
  setting and failed 11 unit tests. Fixed in `bd3e42fb`: the task reads `ADMIN_ACCOUNT_TOKEN`,
  prints how to sign in as `admin`, and the tutorial says the same. `demo prepare` now removes a
  saved session that the Sync API refuses, with a test.
- The live scenarios ran before the rebase. They were not run again.
