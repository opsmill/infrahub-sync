
# LLM Context Guide for `infrahub-sync`

`infrahub-sync` synchronizes data between infra sources and destinations (Infrahub, NetBox, Nautobot, etc.). It uses uv for packaging, a Typer CLI, and Invoke tasks for linting and docs. Examples live in `examples/`.

## Agent Operating Principles

1. **Plan → Ask → Act → Verify → Record** — plan briefly, ask for missing context, act with the smallest change, verify locally, then record with a concise commit or PR note.
2. **Default to read-only and dry runs** — prefer `list`, `diff`, and `generate` before `sync`. Write/apply only with explicit instruction and human approval.
3. **Be specific and reversible** — small, scoped commits. Don't mix large refactors with behavior changes in one PR.
4. **Match existing patterns** — keep CLI, adapters, examples, and directory structure consistent with the codebase.
5. **Idempotency and safety** — favor operations safe to re-run. Never print or guess secrets. Handle timeouts, auth, and network errors explicitly.

## Setup

```bash
pyenv local 3.12.x || use system Python 3.10–3.13
uv sync --extra dev
```

## Required Development Workflow

Run in order before committing:

```bash
uv sync --extra dev
uv run invoke format
uv run invoke lint
```

`invoke lint` runs ruff → pylint → yamllint → ty.

**CLI sanity after changes:**

```bash
uv run infrahub-sync --help
uv run infrahub-sync list --directory examples/
uv run infrahub-sync generate --name from-netbox --directory examples/
```

**Docs** (only if user-facing changes — see [Documentation](#documentation)):

```bash
uv run invoke docs.generate
uv run invoke docs.docusaurus
```

**Policy:**

- New or changed code is Ruff-clean and typed where touched (docstrings, specific exceptions).
- The codebase is clean under ty with no `[[tool.ty.overrides]]` blocks in `pyproject.toml`. Don't reintroduce overrides to mask type errors — fix the underlying issue, or use a targeted `# ty: ignore[<rule>]` with a short TODO at the call site. Ad-hoc check: `uv run ty check .`.
- If you add tests, run `uv run pytest -q`.

## Repository Structure

```text
infrahub-sync/
├─ infrahub_sync/                # Source
│  ├─ cli.py                     # Typer entrypoint
│  ├─ __init__.py                # Public API
│  ├─ utils.py                   # Utilities
│  ├─ potenda/                   # Core sync engine — orchestrates list/diff/generate/sync
│  └─ adapters/                  # Per-system connectors (use existing ones as patterns)
├─ examples/                     # Example sync configs and templates
├─ tasks/                        # Invoke task definitions
├─ docs/                         # Docusaurus (npm project)
├─ tests/                        # Unit and integration tests
├─ pyproject.toml                # uv + tool configs
└─ .github/workflows/            # CI
```

Available adapters (`infrahub_sync/adapters/`): `infrahub`, `netbox`, `nautobot`, `aci`, `prometheus`, `peeringmanager`, `ipfabricsync`, `slurpitsync`, `genericrestapi`.

## CLI Commands

- `infrahub-sync list` — show available sync projects (safe).
- `infrahub-sync diff` — compute differences (safe).
- `infrahub-sync generate` — generate Python from YAML config (servers required).
- `infrahub-sync sync` — perform synchronization (servers and approval required).

## Configuration and Examples

- YAML config keys: `name`, `source`, `destination`, `order`.
- `source` and `destination` specify adapter names and connection settings.
- `order` defines the sync sequence of object types.
- Defaults often target `localhost`; adjust for real deployments.
- Credentials must come from environment variables or a secret manager. Never commit, print, or log secrets. Keep example configs authentic but sanitized.

## Code Standards

### Python (3.10–3.13)

- Prefer explicit types on new or changed code; public functions and classes get concise docstrings.
- Ruff: formatted and lint-clean. Honor `pyproject.toml`.
- Pylint: fix actionable issues in touched code; some warnings are expected.
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
uv run pytest -q
```

## Documentation

- Update `docs/` for any user-visible changes (flags, config, adapters). Keep examples minimal, accurate, and redacted.
- Generate CLI docs: `uv run invoke docs.generate`
- Build site (run `cd docs && pnpm install` once): `uv run invoke docs.docusaurus`
- Lint Markdown/MDX with `rumdl` (config in `pyproject.toml`; also via `uv run invoke docs.rumdl`):

```bash
uv run rumdl check .   # check
uv run rumdl fmt .     # fix
```

## Invoke Tasks (reference)

`uv run invoke --list` for the full set. Key tasks:

- `format` / `lint` — run all formatters / linters.
- `linter.format-ruff`, `linter.lint-ruff`, `linter.lint-pylint`, `linter.lint-yaml`, `linter.lint-ty`.
- `docs.generate`, `docs.docusaurus`, `docs.rumdl`, `docs.format-rumdl`, `docs.format`, `docs.lint`.
- `tests.tests-unit`, `tests.tests-integration`.

## Known Issues and Limitations

- Optional dependencies (e.g. `pynetbox`, `pynautobot`) may be missing, producing import warnings.
- `generate` and `sync` require running servers (Infrahub, NetBox, Nautobot).
- Docs npm audit may flag dev-only vulnerabilities; they do not affect the Python package.

## Git and PR Process

- Do not force-push on shared branches. Do not amend to hide pre-commit fixes; use a follow-up commit.
- Apply PR labels: `bugs`, `breaking`, `enhancements`, `features` (default `enhancements`).
- Run the required workflow (format → lint → CLI sanity) before a PR.
- Agents must identify themselves (e.g. `Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>` or `🤖 Generated with Copilot`).
- Commit subject: imperative "what changed." Rationale goes in the PR body.
- PR body: problem/tension and solution in one to two short paragraphs; a minimal before/after snippet; any user-visible changes (CLI flags, config keys).

**Approval checklist:**

- [ ] Format and lint clean on changed areas.
- [ ] `uv run ty check .` exits 0; new code typed.
- [ ] CLI behaviors validated (`--help`, `list`, targeted `generate`).
- [ ] Docs updated if flags or config changed.
- [ ] Error handling uses specific exception types and clear messages.

## Review Process

- Read surrounding code and examples; align with established patterns.
- Verify claims via the smallest reproduction (CLI or unit).
- Consider edge cases: auth failures, empty inputs, pagination, rate limits, timeouts.
- Provide specific, actionable feedback.
- Least privilege: touch only the minimal required resources. Avoid collisions with live migrations or active syncs; coordinate via PRs.

If unsure, stop and ask with a concrete question.

## Platform-Specific Notes

This file (`AGENTS.md`) is the single source of truth. Platform-specific files should point here and only contain overrides:

- `CLAUDE.md`, `.github/copilot-instructions.md`, `.cursor/rules/dev-standard.mdc`

Each should include the "Required Development Workflow" block and the "Approval checklist" verbatim.

## Adding a New Adapter

See [`dev/guides/adding-an-adapter.md`](dev/guides/adding-an-adapter.md) for the full
step-by-step procedure. Supporting developer reference lives under `dev/`:

- [Adapter knowledge](dev/knowledge/README.md) — how the sync engine, the adapter contract, schema mapping, and the incremental cache work.
- [Adapter guidelines](dev/guidelines/README.md) — the rules for writing and testing an adapter.
- [Adapter guides](dev/guides/README.md) — adding and testing an adapter, step by step.

Core rule unchanged: provide read-only `list` / `diff` pathways and validate them before enabling `sync`.
