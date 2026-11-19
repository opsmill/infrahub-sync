# Morning report: 024-demo-ready-netbox

Branch `024-demo-ready-netbox`, worktree `.worktrees/024-demo-ready-netbox`. Nothing was pushed and no
pull request was opened. The full log is [overnight-log.md](overnight-log.md); the implementation report
is [opsmill-implement-report.md](opsmill-implement-report.md).

## 1. Done

- **Way 1, try the demo from a clone (P1):** the `demo` preset and `uv run invoke dev.demo`, which starts
  a local Infrahub, Sync and NetBox with the `demo` dataset, then runs `infrahub-sync demo prepare`.
  Run live on 2026-11-19 on isolated Compose projects: 2 min 30 s with a warm Docker cache, then a
  plan of 1,687 creates across 20 kinds. A re-run took 7 s and changed nothing.
- **Way 2a, a colleague's own NetBox and Infrahub (P2):** the command
  `infrahub-sync demo prepare --netbox-url … --infrahub-url …`. It loads the
  `traditional-infrastructure-sot` schema into Infrahub, then registers the NetBox package with both
  addresses written in. It is idempotent: a re-run with the same addresses changes nothing; other
  addresses create a new version. Code in `infrahub_sync/demo/`, CLI in `infrahub_sync/cli.py`.
- **Live run on an isolated stack** (`infrahub-sync-024`, 2026-11-19, then removed):
  - from the host, four runs gave `registered` v1, `unchanged` v1, `versioned` v2 and `unchanged` v1,
    as the data model states;
  - from the image with no checkout, the run loaded the 16 bundled schema files and gave
    `versioned` v3.
- **Schema conflict:** against an Infrahub that holds a conflicting node, `demo prepare` stopped
  with exit 1, named the conflict and registered nothing. The smoke schema of `dev.preview` does
  not conflict, so the quickstart now uses a conflicting schema.
- **Fix found by the live run:** the NetBox tasks attached the worker of the fixed
  `infrahub-sync-dev` project, so a stack under another project name changed a stack it did not
  own. Commit `e7921073` fixes it, with tests.
- **Web interface:** **Start run** plans the latest version (from the frontend source). The
  tutorial now says how to plan an earlier version.
- **Prerequisites of way 2b, a Styrmin demo:** the schema and the package ship inside the wheel and the
  image (ADR 0048, `dev/adr/0048-demo-assets-ship-in-the-wheel.md`), so a Styrmin driver can call
  `infrahub-sync demo prepare` from the published image. The Styrmin drivers themselves are not part
  of this work.
- **Pre-release images (P3):** documented in
  `docs/docs/development/guides/publishing-an-image.md`, section "A pre-release image of the V3 line".
  No code was needed.
- **Tutorial (P4):** `docs/docs/tutorials/try-the-netbox-demo.mdx` covers the three ways and ends with a
  "Send feedback" section.
- **Rebase onto `feature/v3-develop` (2026-11-19):** onto `1dc5e204`. The local accounts change
  broke `dev.demo` (it read the removed `BEARER_TOKENS` setting) and changed the web sign-in to an
  account name and a password; fixed in `bd3e42fb`. The tutorial was then reworked and retitled
  "Import NetBox demo data in one command".
- **Checks after the rebase, on `bd3e42fb` (2026-11-19):** unit tier 10,760 passed;
  `invoke lint` and `ruff check .` pass (`ty` reports 29 warnings in files this branch does not
  change); `docs.lint` passes; `docs.docusaurus` built the site; `tests.check-310` passed all
  three Python 3.10 legs (7,455 passed). After the docs rework, `docs.lint` and
  `docs.docusaurus` passed again and every new link anchor exists in the built site.

## 2. Blocked

1. **Which channel receives the feedback?** The tutorial asks testers to use "the channel named in the
   invitation". Recommended: an internal Slack channel, named in the invitation message, not a public
   GitHub issue, because feedback can name customers.
2. **Where does the Styrmin work start?** Recommended: a new spec in the `styrmin` repository for an
   `infrahub-sync` driver that runs `infrahub-sync demo prepare` and a NetBox driver. It needs the
   Helm chart from spec 018 and a pre-release image.

## 3. Advisor decisions

1. **Q:** the live scenarios would touch stacks that are not ours. **A:** never touch them; try once
   at the end on an isolated project if every step honours the override, else skip. **Next:** the
   NetBox tasks do not honour the override, so the `dev.demo` scenarios were skipped.
2. **Q:** is the run complete? **A:** no: write this report, check the image workflow dispatch, rerun
   the docs and Python 3.10 checks, and try one live `demo prepare` run on an isolated project if the
   021 and 022 sessions agree. **Next:** both sessions agreed; the live run passed and the stack was
   removed.
3. **Q:** may I fix the NetBox attach and run `dev.demo` on isolated projects? **A:** yes, with
   read-only checks first, a before-and-after snapshot of the other projects, and no apply.
   **Next:** fixed in `e7921073`, ran scenarios 1 to 4; the snapshot matched after teardown.

## 4. Not done

- The apply in quickstart scenario 1: AGENTS.md requires human approval for an apply, so the plan
  was created but not applied.
- The web interface steps in the tutorial were written from the frontend source and not run.
- The live scenarios ran before the rebase and were not run again after it.
- The Styrmin drivers: out of scope for this repository.
- The simplify review: it edits code and the run was unattended.
- `/speckit.opsmill.extract` and a pull request: left for you.

## 5. Risks

- Repository configuration changed: `.github/file-filters.yml`, `.dockerignore`, `.yamllint.yml`
  (ignores `/.dev`) and the Vale vocabulary (`Kubernetes`, `Styrmin`). The CI Vale vocabulary can
  differ from the local one.
- `main` has no `ci-docker-image.yml`. A pre-release dispatch uses `--ref feature/v3-develop`; a
  dispatch from another non-main branch worked on 2026-10-01, but this branch was not tried.
- After the addresses go A, B, A, the command prints version 1, but **Start run** plans the latest
  version, 2. The tutorial explains this; a colleague who skips that paragraph plans the wrong
  NetBox address.
- The 2 min 30 s of SC-001 had the base images already pulled. A first run on an empty Docker
  cache takes longer.
- The `infrahub-sync-dev` containers were removed by someone else since last night; its 8 volumes
  and one `infrahub-sync-netbox` volume remain. This run did not touch them.
- Chunk 2 rebuilt the shared `infrahub-sync:dev` image tag.
- `tests/demo/test_wheel_assets.py` builds a wheel in the unit tier and may download build
  dependencies.
- `build_inputs_hash` in `tasks/dev.py` does not hash the two asset paths, so a change to them does
  not trigger a rebuild of the development image.
