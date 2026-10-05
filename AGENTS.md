
# LLM Context Guide for `infrahub-sync`

`infrahub-sync` synchronizes data between infra sources and destinations (Infrahub, NetBox, Nautobot, etc.). It uses uv for packaging, a Typer CLI, and Invoke tasks for linting and docs. Examples live in `examples/`.

## Agent Operating Principles

1. **Plan → Ask → Act → Verify → Record** — plan briefly, ask for missing context, act with the smallest change, verify locally, then record with a concise commit or PR note.
2. **Default to read-only and dry runs** — prefer `configs` inspection, `runs plan`, and `diff` before `sync`. Write/apply only with explicit instruction and human approval.
3. **Be specific and reversible** — small, scoped commits. Don't mix large refactors with behavior changes in one PR.
4. **Match existing patterns** — keep CLI, adapters, examples, and directory structure consistent with the codebase.
5. **Idempotency and safety** — favor operations safe to re-run. Never print or guess secrets. Handle timeouts, auth, and network errors explicitly.

## Setup

Use Python 3.11–3.13 for the full development profile. The former private
`opsmill/prefect-extras` Git dependency is vendored at `opsmill_prefect_extras/`
(frozen; see its `VENDORED.md`), so no special repository access is required:

```bash
uv sync --extra dev --extra prefect --extra service
```

On Python 3.10, install the direct Prefect profile instead. The Sync service supports
Python 3.11–3.13 only:

```bash
uv sync --python 3.10 --extra dev --extra prefect
```

## Required Development Workflow

Run the setup command for your Python profile, then run these commands in order before
committing:

```bash
uv run invoke format
uv run invoke lint
```

`invoke lint` runs rumdl → ruff → pylint → yamllint → ty, and stops after the first gate
failure. The Pylint leg captures Pylint's JSON report and fails on any diagnostic. The Pylint gate and the
archive exclusions that keep `rumdl fmt` away from incompatible historical artifacts are
documented in [`develop/knowledge/quality-gates.md`](docs/docs/develop/knowledge/quality-gates.md).

The `prefect` extra is not optional for development: without it `ty` cannot resolve
`infrahub_sync/orchestration/`'s imports and `tests/orchestration/test_flow.py` skips
itself whole. On Python 3.11–3.13, the `service` extra is required too; otherwise `ty`
cannot resolve the Sync service's FastAPI and Prefect imports. On Python 3.10,
`invoke linter.lint-ty` and `invoke linter.lint-pylint` exclude `infrahub_sync/service`
(ty also excludes `tests/service`), matching CI's direct Prefect gate. CI also runs a base-install job that keeps the Prefect-free
guarantee honest.

**CLI sanity after changes:**

```bash
uv run infrahub-sync --help
uv run infrahub-sync configs --help
uv run infrahub-sync runs --help
```

The `from-netbox` example check is integration-backed. It needs a local NetBox with
the pinned `demo` dataset, the preview stack, and the schema library. Follow
[The `from-netbox` example check](docs/docs/develop/guidelines/testing-tiers.md#the-from-netbox-example-check).

**Docs** (only if user-facing changes — see [Documentation](#documentation)):

```bash
uv run invoke docs.generate
uv run invoke docs.docusaurus
```

**Policy:**

- New or changed code is Ruff-clean and typed where touched (docstrings, specific exceptions).
- The codebase is clean under ty with no `[[tool.ty.overrides]]` blocks in `pyproject.toml`. Don't reintroduce overrides to mask type errors — fix the underlying issue, or use a targeted `# ty: ignore[<rule>]` with a short TODO at the call site. Run `uv run ty check .` in the full Python 3.11–3.13 profile (the frozen vendored upstream tests are excluded via `[tool.ty.src]` in `pyproject.toml`; the vendored package itself stays checked). On Python 3.10, run `uv run ty check --exclude infrahub_sync/service --exclude tests/service .`.
- If you add tests, run `uv run invoke tests.tests-unit`.

## Repository Structure

```text
infrahub-sync/
├─ infrahub_sync/                # Source
├─ examples/                     # Example sync configs and templates
├─ tasks/                        # Invoke task definitions
├─ docs/                         # Docusaurus (npm project)
├─ tests/                        # Unit and integration tests
├─ pyproject.toml                # uv + tool configs
└─ .github/workflows/            # CI
```

[`develop/knowledge/repository-tour.md`](docs/docs/develop/knowledge/repository-tour.md) is the
canonical inventory of `infrahub_sync/`. It maps every current package — the CLI and its HTTP
client, configuration admission, the service and worker, runtime schema, execution, plans,
product storage, adapters, cache, generator, orchestration, the Potenda engine, plugin loading
and dependency graphs — and separates registered V3 execution from development-only and
historical material. Read it rather than inferring the layout from the tree above.

Available adapters (`infrahub_sync/adapters/`): `infrahub`, `netbox`, `nautobot`, `aci`, `prometheus`, `peeringmanager`, `ipfabricsync`, `slurpitsync`, `genericrestapi`.

## CLI Commands

- `infrahub-sync configs` — register and inspect configuration packages (safe).
- `infrahub-sync runs plan` — review a saved plan (safe).
- `infrahub-sync diff` — create a plan run and review its summary (Sync API required).
- `infrahub-sync sync` — perform synchronization (Sync API and approval required).
- `infrahub-sync apply` — apply a reviewed plan by checksum (Sync API and approval required).

## Configuration and Examples

- YAML config keys: `name`, `source`, `destination`, `order` (optional).
- `source` and `destination` specify adapter names and connection settings.
- `order` overrides the sync sequence of object types; when omitted, infrahub-sync computes
  a write order from schema references instead.
- Defaults often target `localhost`; adjust for real deployments.
- Credentials must come from environment variables or a secret manager. Never commit, print, or log secrets. Keep example configs authentic but sanitized.

## Code Standards

### Python (3.10–3.13)

- Prefer explicit types on new or changed code; public functions and classes get concise docstrings.
- Ruff: formatted and lint-clean. Honor `pyproject.toml`.
- Pylint: passes on a clean checkout and is a failing CI step; fix every finding you introduce.
  Details are in [`develop/knowledge/quality-gates.md`](docs/docs/develop/knowledge/quality-gates.md).
- ty: included in `uv run invoke lint`; do not increase the error count.
- Raise specific exceptions; avoid broad `except Exception:`.

### CLI and UX

- Predictable, idempotent commands with clear validation and errors.
- No secrets in logs or tracebacks.
- Prefer explicit flags over implicit behavior.

### Logging

- Use `structlog` for structured logging (not `print`). Include context (request IDs, endpoints, object counts) but never secrets.

## Testing

Add targeted tests for new features or bug fixes:

- Unit tests for `utils` and adapter edge cases (timeouts, 401/403, empty pages).
- Parametrized tests for config parsing; prefer parametrization over loops.
- Mark network/integration tests opt-in (e.g. `-m integration`).
- Keep tests atomic and single-purpose.

```bash
uv run invoke tests.tests-unit
```

This runs the offline unit tier. The live tiers and their settings are in
[`develop/guidelines/testing-tiers.md`](docs/docs/develop/guidelines/testing-tiers.md).

## Documentation

- Update `docs/` for any user-visible changes (flags, config, adapters). Keep examples minimal, accurate, and redacted.
- Generate CLI docs: `uv run invoke docs.generate`
- Build site (run `cd docs && pnpm install --frozen-lockfile` once; `pnpm` must be on your `PATH`, for example through Corepack): `uv run invoke docs.docusaurus`
- Lint Markdown/MDX with `rumdl` (config in `pyproject.toml`; also via `uv run invoke docs.rumdl`):

```bash
uv run rumdl check .   # check
uv run rumdl fmt .     # fix
```

## Changelog

Release notes are written by contributors, not generated from PR titles. Every pull request must add a news fragment under `changelog/`, including pull requests into `feature/v3-develop`. The `changelog-check.yml` workflow that enforces this for `main` is disabled at present: its `pull_request` trigger is commented out, and a manual dispatch fails because it has no pull request to check. Reviewers check for the fragment.

Create one with towncrier, naming it after the issue or PR number:

```bash
uv run --extra dev towncrier create -c "Short description of what changed." 123.fixed.md
```

The file must be a direct child of `changelog/` named `<id>.<type>.md`. The seven types are configured in `[tool.towncrier]` in `pyproject.toml`:

`security`, `removed`, `deprecated`, `added`, `changed`, `fixed`, `housekeeping`

Use `+` as the id for a change with no issue number (`+short-slug.housekeeping.md`). Nested paths and unknown types are ignored by towncrier, so reviewers reject them rather than let an entry vanish at release time (`changelog-check.yml` rejects them too once its trigger is restored). A fragment that is empty or whitespace-only fails the release build, which names the file.

Label a pull request `ci/skip-changelog` when it needs no entry, for example a dependency bump or a typo fix. Dependabot applies that label itself.

Every normal pull request into `main` must also carry exactly one release-intent label — `changes/major`, `changes/minor`, or `changes/patch` — and `release-label-check.yml` fails the PR if it does not. These labels alone determine the version bump. Dependabot and `update-infrahub-sdk.yml` apply `changes/patch` themselves; generated `chore(release):` pull requests are exempt because they apply, rather than introduce, that release intent.

**Versions and `CHANGELOG.md` are never edited manually.** For a V2 release from `main`, merging does not prepare the release: dispatch `trigger-push-stable.yml` from Actions with `main` selected, which opens a `chore(release)` pull request carrying the version bump and the changelog assembled from the fragments it consumes. Merging that pull request creates the tag and publishes the GitHub Release. Do not bump `pyproject.toml`, edit `CHANGELOG.md`, or create tags yourself. See [RELEASING.md](RELEASING.md). V3 pre-release candidates are built by `workflow-candidate.yml` instead; see [`develop/guides/qualifying-an-internal-candidate.md`](docs/docs/develop/guides/qualifying-an-internal-candidate.md).

## Invoke Tasks (reference)

`uv run invoke --list` for the full set. Key tasks:

- `format` / `lint` — run all formatters / linters.
- `linter.format-ruff`, `linter.lint-ruff`, `linter.lint-pylint`, `linter.lint-yaml`, `linter.lint-ty`.
- `docs.generate`, `docs.docusaurus`, `docs.rumdl`, `docs.format-rumdl`, `docs.format`, `docs.lint`.
- `tests.tests-unit`, `tests.tests-integration`.

## Known Issues and Limitations

- Optional dependencies (e.g. `pynetbox`, `pynautobot`) may be missing, producing import warnings.
- `diff`, `sync`, and `apply` require a running Sync API and its destination servers.
- Docs npm audit may flag dev-only vulnerabilities; they do not affect the Python package.

## Git and PR Process

- Do not force-push on shared branches. Do not amend to hide pre-commit fixes; use a follow-up commit.
- Apply exactly one `changes/*` release-intent label as described above.
- Run the required workflow (format → lint → CLI sanity) before a PR.
- Agents must identify themselves (e.g. `Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>` or `🤖 Generated with Copilot`).
- Commit subject: imperative "what changed." Rationale goes in the PR body.
- PR body: problem/tension and solution in one to two short paragraphs; a minimal before/after snippet; any user-visible changes (CLI flags, config keys).

**Approval checklist:**

- [ ] Format and lint clean on changed areas.
- [ ] The type-check command for the active Python profile exits 0; new code typed.
- [ ] CLI behaviors validated (`--help`, `configs --help`, `runs --help`).
- [ ] Docs updated if flags or config changed.
- [ ] News fragment added under `changelog/`, or `ci/skip-changelog` applied.
- [ ] Error handling uses specific exception types and clear messages.

## Review Process

- Read surrounding code and examples; align with established patterns.
- Verify claims via the smallest reproduction (CLI or unit).
- Consider edge cases: auth failures, empty inputs, pagination, rate limits, timeouts.
- Provide specific, actionable feedback.
- Least privilege: touch only the minimal required resources. Avoid collisions with live migrations or active syncs; coordinate via PRs.

If unsure, stop and ask with a concrete question.

## Platform-Specific Notes

This file (`AGENTS.md`) is the single source of truth. Platform-specific files point here and contain only overrides:

- `CLAUDE.md` imports this file with `@AGENTS.md`.
- `.github/copilot-instructions.md` is a symbolic link to this file, so it always has the same content.

A platform file that copies text instead must include the "Required Development Workflow" block and the "Approval checklist" verbatim.

## Adding a New Adapter

See [`develop/guides/adding-an-adapter.md`](docs/docs/develop/guides/adding-an-adapter.md) for the full
step-by-step procedure. Supporting developer reference lives under `docs/docs/develop/`:

- [Adapter knowledge](docs/docs/develop/knowledge/index.md) — how the sync engine, the adapter contract, schema mapping, and the incremental cache work.
- [Adapter guidelines](docs/docs/develop/guidelines/index.md) — the rules for writing and testing an adapter.
- [Adapter guides](docs/docs/develop/guides/index.md) — adding and testing an adapter, step by step.

Core rule unchanged: provide a read-only `diff` pathway and validate it before enabling `sync`.

## Beyond Adapters

`docs/docs/develop/` is not adapter-only. When the work is not an adapter, start here:

- [The shared execution surface](docs/docs/develop/knowledge/execution-surface.md) — the typed entry point
  used by the service worker and direct Python callers. The CLI submits runs through the Sync HTTP API;
  the direct Prefect flow calls the execution module through `run_remote_request`.
- [Prefect orchestration](docs/docs/develop/knowledge/orchestration-prefect.md) — the optional
  orchestration integration and its import boundary.
- [Quality gates](docs/docs/develop/knowledge/quality-gates.md) — what the lint and format aggregates
  really do, and the Pylint gate.
- [Testing](docs/docs/develop/guidelines/testing.md) — repository-wide test rules.
- [Secret redaction](docs/docs/develop/guidelines/secret-redaction.md) — required reading before adding any
  failure path that crosses a process boundary.
- [Decision records](dev/adr/README.md) — why the architecture is shaped the way it is.
