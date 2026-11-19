# Morning report: 024-demo-ready-netbox

Branch `024-demo-ready-netbox`, worktree `.worktrees/024-demo-ready-netbox`. Nothing was pushed and no
pull request was opened. The full log is [overnight-log.md](overnight-log.md); the implementation report
is [opsmill-implement-report.md](opsmill-implement-report.md).

## 1. Done

- **Way 1, try the demo from a clone (P1):** the `demo` preset and `uv run invoke dev.demo`, which starts
  a local Infrahub and Sync, then runs `infrahub-sync demo prepare`. Tested against fakes in the unit
  tier. `dev.demo` itself was not run on this machine (see "Not done").
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
- **Prerequisites of way 2b, a Styrmin demo:** the schema and the package ship inside the wheel and the
  image (ADR 0048, `dev/adr/0048-demo-assets-ship-in-the-wheel.md`), so a Styrmin driver can call
  `infrahub-sync demo prepare` from the published image. The Styrmin drivers themselves are not part
  of this work.
- **Pre-release images (P3):** documented in
  `docs/docs/development/guides/publishing-an-image.md`, section "A pre-release image of the V3 line".
  No code was needed.
- **Tutorial (P4):** `docs/docs/tutorials/try-the-netbox-demo.mdx` covers the three ways and ends with a
  "Send feedback" section.
- **Checks on the final tree:** unit tier 9003 passed; `invoke lint`, `ruff check .` and `docs.lint`
  pass; `tests.check-310`, `docs.generate` and `docs.docusaurus` reran this morning (results in the
  log).

## 2. Blocked

1. **May the stopped `infrahub-sync-dev` and `infrahub-sync-netbox` Compose projects be removed?** They
   were not created by this run. The NetBox tasks attach to the fixed `infrahub-sync-dev` worker
   (`tasks/netbox.py:472`, `:747`), so `dev.demo` with NetBox cannot run without touching them.
   Recommended: remove them yourself if they hold nothing you need, then run quickstart scenarios 1, 2
   and 4 and record the time for SC-001.
2. **Which channel receives the feedback?** The tutorial asks testers to use "the channel named in the
   invitation". Recommended: an internal Slack channel, named in the invitation message, not a public
   GitHub issue, because feedback can name customers.
3. **Where does the Styrmin work start?** Recommended: a new spec in the `styrmin` repository for an
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

## 4. Not done

- `dev.demo` with NetBox, a plan from the demo, and the schema conflict scenario (T016, T022 live
  parts), because of blocked item 1.
- The web interface steps in the tutorial were written from the frontend source and not run.
- The Styrmin drivers: out of scope for this repository.
- The simplify review: it edits code and the run was unattended.
- `/speckit.opsmill.extract` and a pull request: left for you.

## 5. Risks

- Repository configuration changed: `.github/file-filters.yml`, `.dockerignore`, `.yamllint.yml`
  (ignores `/.dev`) and the Vale vocabulary (`Kubernetes`, `Styrmin`). The CI Vale vocabulary can
  differ from the local one.
- `main` has no `ci-docker-image.yml`. A pre-release dispatch uses `--ref feature/v3-develop`; a
  dispatch from another non-main branch worked on 2026-10-01, but this branch was not tried.
- Which version the web interface's **Start run** uses after the addresses go A, B, A is not known.
  The command returns v1 in that case, but the newest version is v2.
- Chunk 2 rebuilt the shared `infrahub-sync:dev` image tag.
- `tests/demo/test_wheel_assets.py` builds a wheel in the unit tier and may download build
  dependencies.
- `build_inputs_hash` in `tasks/dev.py` does not hash the two asset paths, so a change to them does
  not trigger a rebuild of the development image.
