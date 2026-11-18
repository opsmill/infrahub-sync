# Tasks: A NetBox demo of Sync 3.x, ready to try locally and from a Kubernetes deployment

**Input**: [spec.md](spec.md), [plan.md](plan.md), [research.md](research.md),
[data-model.md](data-model.md), [contracts/](contracts/), [quickstart.md](quickstart.md),
[critique](critiques/critique-20261118.md)

**Tests**: included. `AGENTS.md` requires targeted tests for new features.

**Order note**: User Story 1 (local demo) calls the command of User Story 2 (FR-005), so the command
itself is in the Foundational phase. User Story 2's own phase adds what makes the command run from
the image without a checkout. Work only in this repository; never edit `../styrmin` or
`../infrahub-helm-sync-chart`.

**Verify after every phase**:

```bash
uv run invoke format
uv run invoke lint
uv run ruff check .
uv run invoke tests.tests-unit
```

## Phase 1: Setup

- [ ] T001 Create the package `infrahub_sync/demo/` with `__init__.py` (module docstring: what the demo preparation does, that it never plans or applies) in `infrahub_sync/demo/__init__.py`, and an empty test package `tests/demo/__init__.py`

## Phase 2: Foundational (the `demo prepare` command)

- [ ] T002 [P] Write tests for the asset resolver in `tests/demo/test_assets.py`: from the source tree it returns the 16 YAML files of `tests/data/nightly_schema/traditional-infrastructure-sot/` sorted by name and the text of `examples/netbox_to_infrahub/package.yml`; when the bundled directory `infrahub_sync/demo/assets/` exists it is preferred (simulate with `tmp_path` and monkeypatching the bundled root); a missing asset raises the module's own error naming the path
- [ ] T003 [P] Write tests for the package rewrite in `tests/demo/test_package.py`: moved from the `local_package_text` cases in `tests/test_netbox_tasks.py` (each shipped URL line replaced exactly once; a package with a URL line missing or doubled is refused; a replacement equal to the other shipped URL still works), plus the package `name` read from the text
- [ ] T004 Implement `infrahub_sync/demo/assets.py`: `schema_files() -> list[Path]` and `netbox_package_text() -> str`, reading `importlib.resources.files("infrahub_sync.demo") / "assets"` first (`schema/*.yml`, `netbox/package.yml`), falling back to the two repository paths relative to the repository root (research R-1); raise `DemoAssetError` (new, in `infrahub_sync/demo/errors.py`) naming the missing path
- [ ] T005 Move `local_package_text`, `SHIPPED_NETBOX_URL`, `SHIPPED_INFRAHUB_URL` from `tasks/netbox.py` to `infrahub_sync/demo/package.py` (raise `DemoPackageError` instead of `NetboxError`; keep the message), add `package_name(text) -> str`; make `tasks/netbox.py` import them and wrap the error into `NetboxError` so the task's behaviour and messages stay the same; update `tests/test_netbox_tasks.py` imports
- [ ] T006 [P] Write tests for preparation in `tests/demo/test_prepare.py` with a fake Sync client and a fake schema loader: (a) empty Infrahub and no configuration → schema `loaded`, configuration `registered` v1, idempotency key `demo-prepare-register-<sha12>`; (b) second run, same addresses → schema `unchanged`, configuration `unchanged` (version call answers 200); (c) other NetBox address → `versioned` v2 with key `demo-prepare-version-<config_id>-<sha12>`; (d) two configurations with the package name → the newest is versioned and the result names it; (e) schema load errors → `DemoSchemaError`, no Sync client call; (f) the result and any message never contain the token values (use canary strings); (g) nothing in the module reads or writes `INFRAHUB_SYNC_REQUIRE_APPROVAL`
- [ ] T007 Implement `infrahub_sync/demo/prepare.py`: a `PreparationResult` dataclass with the fields of [data-model.md](data-model.md); `load_schema(address, token, files, timeout) -> Literal["loaded","unchanged"]` using `infrahub_sdk.InfrahubClientSync` and `client.schema.load(schemas=[yaml.safe_load(...)...], wait_until_converged=True)`, reading `schema_updated` and `errors` (research R-4; raise `DemoSchemaError` with the error text); `register_package(client, text, reason) -> (config_id, version, state)` per research R-3 and plan step 4; `prepare(...)` that runs the two in order and stops before registering when the schema load fails
- [ ] T008 Add the `demo` Typer group with `prepare` to `infrahub_sync/cli.py` (register only; logic stays in `infrahub_sync/demo/`), options per [contracts/demo-prepare-cli.md](contracts/demo-prepare-cli.md): `--netbox-url`, `--infrahub-url`, `--infrahub-address`, `--schema-timeout` (default 120), `--reason`, `--json`; read `INFRAHUB_API_TOKEN` (missing → exit 2 naming the variable); use the CLI's existing Sync client construction and `_client_errors()` handler; print the text summary of the contract or one JSON object
- [ ] T009 [P] Write CLI tests in `tests/cli/test_demo_prepare.py` with Typer's `CliRunner` and the fakes of T006: exit 2 without `INFRAHUB_API_TOKEN`; exit 1 and the cause on a schema error; exit 0 text and `--json` output; no token value in output
- [ ] T010 Run the phase verification commands; fix every finding

**Checkpoint**: `uv run infrahub-sync demo prepare --help` works from the source tree.

## Phase 3: User Story 1 - Try the NetBox demo locally with one command (P1) 🎯 MVP

**Goal**: `uv run invoke dev.demo` from a fresh clone leaves the stack ready for a first plan.

**Independent test**: [quickstart.md](quickstart.md) scenarios 1 and 2.

- [ ] T011 [P] [US1] Write tests in `tests/dev_stack/` next to the existing preset tests (find them with `grep -rln "PRESETS\|preset_named" tests/`): the `demo` preset exists with a local Infrahub and NetBox and no smoke seed; `preview` is unchanged; `dev.demo` passes the tokens through the process environment and never as arguments; it prints the reload command for an existing NetBox; it prints no token the user set (only `infrahub-sync-dev-token` when that development token is in use)
- [ ] T012 [US1] Add `Preset("demo", "the `infrahub` preset and the local NetBox with the `demo` dataset, ready for the NetBox demo", local_infrahub=True, netbox=True)` to `PRESETS` in `tasks/dev.py`, and make every place that runs the smoke seed for `preview` skip it for `demo`; make `_stack_env` give the NetBox credential for `demo` as for `preview`
- [ ] T013 [US1] Add the `dev.demo` task to `tasks/dev.py` per [contracts/dev-demo-task.md](contracts/dev-demo-task.md): start the `demo` preset; seed the `demo` dataset only when NetBox is new, otherwise say so, name the dataset when `tasks/netbox.py` can tell, and print `uv run invoke netbox.seed --dataset demo`; run `uv run infrahub-sync demo prepare --netbox-url http://netbox:8080 --infrahub-url http://infrahub-server:8000 --infrahub-address <published Infrahub address>` on the host with `INFRAHUB_API_TOKEN`, `INFRAHUB_SYNC_API_URL` and the Sync token in the environment; then print the web interface address (the Sync API address), which token to enter, the configuration ID and version, and the branch-create and plan commands
- [ ] T014 [US1] Verify research R-9: build the development image (`uv run invoke dev.build`) and check that `GET /` on the Sync API serves the web interface; if it does not, make `dev.demo` print the `dev.frontend` instruction instead of the root address, and record the result in `research.md` R-9
- [ ] T015 [US1] Register the new task wherever the repository lists tasks: `tests/test_task_inventory.py`, `tests/test_task_names_in_docs.py` expectations, the task reference in `docs/docs/development-stack.mdx`, and `AGENTS.md` "Invoke Tasks" (`dev.start` presets list and `dev.demo` shortcut)
- [ ] T016 [US1] Run the phase verification commands; then run quickstart scenarios 1 and 2 if Docker is available, and record the elapsed time of scenario 1 and the plan counts in `dev/specs/024-demo-ready-netbox/opsmill-implement-report.md` evidence (if Docker is not available, say so there)

**Checkpoint**: User Story 1 works on its own.

## Phase 4: User Story 2 - Prepare any Sync deployment without a checkout (P2)

**Goal**: the published image runs `demo prepare` with no checkout.

**Independent test**: [quickstart.md](quickstart.md) scenarios 3 and 4.

- [ ] T017 [US2] Add to `pyproject.toml` under `[tool.hatch.build.targets.wheel.force-include]`: `"examples/netbox_to_infrahub/package.yml" = "infrahub_sync/demo/assets/netbox/package.yml"` and `"tests/data/nightly_schema/traditional-infrastructure-sot" = "infrahub_sync/demo/assets/schema"`; make sure the source distribution includes both sources
- [ ] T018 [US2] In `Dockerfile`, copy `examples/netbox_to_infrahub/package.yml` and `tests/data/nightly_schema/traditional-infrastructure-sot/` into the build context at their repository paths before the `uv sync --no-editable` that builds the project; update `tests/image/test_build_context.py` (or the `.dockerignore` it checks) so the two paths are allowed in the context
- [ ] T019 [P] [US2] Write `tests/demo/test_wheel_assets.py`: build the wheel into `tmp_path` with `uv build --wheel` (mark it the way other build tests are marked, so the unit tier stays offline and fast) and assert it contains the 16 schema files and the package under `infrahub_sync/demo/assets/`, byte-identical to the sources
- [ ] T020 [P] [US2] Write `tests/image/test_demo_assets.py` following the existing `tests/image/` fixtures: in the built image, `infrahub-sync demo prepare --help` exits 0 and `python -c` resolving the assets returns 16 schema files and the package from the bundled path
- [ ] T021 [US2] Write the ADR `dev/adr/00NN-demo-assets-ship-in-the-wheel.md` (next free number; follow `dev/adr/README.md`; cite no internal sources): the decision of research R-1, the alternatives, and the consequence that the PyPI package carries the demo schema and package
- [ ] T022 [US2] Run the phase verification commands; run quickstart scenarios 3 and 4 if Docker is available and record the result in the implement report

**Checkpoint**: a Styrmin driver or a chart job can call `infrahub-sync demo prepare` from the image.

## Phase 5: User Story 3 - Publish pre-release images (P2)

**Goal**: maintainers know how to publish a pre-release image; no code (research R-6).

- [ ] T023 [US3] Check that `docs/docs/development/guides/publishing-an-image.md` covers a dispatch from `feature/v3-develop` with a tag such as `3.0.0-rc.1`, both platforms, signature, SBOM, and `latest` unchanged; add only what is missing. Do not dispatch the workflow

## Phase 6: User Story 4 - Read how to try the demo and where to send feedback (P3)

**Goal**: one page for colleagues.

- [ ] T024 [US4] Write `docs/docs/tutorials/try-the-netbox-demo.mdx` in the house style of the other tutorials (frontmatter `title`, `description`; plain English; no marketing): who it is for; prerequisites; `uv run invoke dev.demo`; a first plan on a new branch from the web interface and from the CLI; review and apply; that approval is off in the demo and why the demo does not show it (it is changing); preparing a Kubernetes deployment with `infrahub-sync demo prepare` and its options; that the Styrmin drivers are not available yet; pre-release images, linking the publishing guide; "When a run fails" pointing to the existing section; "Send feedback" per research R-8 (the channel named in the invitation, what to include, no public tracker); clean-up with `dev.destroy` and `netbox.destroy`
- [ ] T025 [US4] Add the page to the docs sidebar next to the other tutorials (find the sidebar file with `grep -rn "netbox-demo-to-infrahub" docs/sidebars*`), and add one line pointing to it in `docs/docs/development-stack.mdx` ("Run a first sync"), `docs/docs/tutorials/netbox-demo-to-infrahub.mdx` (the "Other routes" tip) and `examples/netbox_to_infrahub/README.md`
- [ ] T026 [US4] Regenerate the CLI reference with `uv run invoke docs.generate` and run `uv run invoke docs.lint` (rumdl and Vale); fix findings

## Phase 7: Polish

- [ ] T027 Add the news fragment `changelog/+demo-prepare.added.md`: "Added `infrahub-sync demo prepare`, which loads the NetBox demo's destination schema into an Infrahub and registers the NetBox package for a Sync deployment, and the `dev.demo` task, which prepares a local demo with one command."
- [ ] T028 Run the full verification: `uv run invoke format`, `uv run invoke lint`, `uv run ruff check .`, `uv run invoke tests.tests-unit`, and the CLI sanity commands (`uv run infrahub-sync --help`, `configs --help`, `runs --help`, `demo --help`)
- [ ] T029 Search the diff for token values and for `Claude-Session` trailers; confirm no file outside this repository changed

## Dependencies

- Phase 1 → Phase 2 → Phase 3 (US1) and Phase 4 (US2), which can run in parallel after Phase 2.
- Phase 5 (US3) has no dependency.
- Phase 6 (US4) depends on the command options (Phase 2) and `dev.demo` (Phase 3).
- Phase 7 last.

## Parallel examples

- Phase 2: T002, T003, T006 (test files) together; then T004 and T005; then T007, T008, T009.
- Phase 4: T019 and T020 together after T017 and T018.

## Implementation strategy

MVP is Phases 1 to 3: the local demo. Phase 4 unblocks the Styrmin drivers; Phases 5 and 6 are
documentation. Each phase ends with the verification commands and its own commit.
