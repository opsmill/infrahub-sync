---
applyTo: '**'
---

# LLM Context Guide for `infrahub-sync`

`infrahub-sync` synchronizes data between infra sources and destinations (Infrahub, NetBox, Nautobot, etc.). It uses Poetry for packaging, a Typer CLI, and Invoke tasks for linting and docs. Examples live in `examples/`.

## Agent Operating Principles

1. **Plan → Ask → Act → Verify → Record**
   Plan briefly, ask for missing context, act with the smallest change, verify locally, then record with a concise commit or PR note.

2. **Default to read-only and dry runs**
   Prefer `list`, `diff`, and `generate` before `sync`. Write/apply only with explicit instruction and human approval.

3. **Be specific and reversible**
   Use small, scoped commits. Do not mix large refactors with behavior changes in the same PR.

4. **Match existing patterns**
   Keep CLI, adapters, examples, and directory structure consistent with the codebase.

5. **Idempotency and safety**
   Favor operations that are safe to re-run. Use dry runs. Never print or guess secrets. Handle timeouts, auth, and network errors explicitly.

## Required Development Workflow

Run these in order before committing.

```bash
poetry install
poetry run invoke format
poetry run invoke lint
poetry run mypy infrahub_sync/ --ignore-missing-imports
```

**Policy:**

- New or changed code is Ruff-clean and typed where touched (docstrings, specific exceptions).
- Do not increase existing mypy debt. If needed, use targeted `# type: ignore[<code>]` with a short TODO.
- If you add tests, run `poetry run pytest -q`.

**CLI sanity after changes:**

```bash
poetry run infrahub-sync --help
poetry run infrahub-sync list --directory examples/
poetry run infrahub-sync generate --name from-netbox --directory examples/
```

**Docs:** (only if user-facing changes)

```bash
poetry run invoke docs.generate
poetry run invoke docs.docusaurus
```

## Repository Structure

```text
infrahub-sync/
├─ infrahub_sync/                # Source
│  ├─ cli.py                     # Typer entrypoint
│  ├─ __init__.py                # Public API
│  ├─ utils.py                   # Utilities
│  ├─ potenda/                   # Core sync engine
│  └─ adapters/                  # NetBox/Nautobot/Infrahub adapters
├─ examples/                     # Example sync configs
├─ tasks/                        # Invoke task definitions
├─ docs/                         # Docusaurus (npm project)
├─ tests/                        # Scaffolding (no tests yet)
├─ pyproject.toml                # Poetry + tool configs
└─ .github/workflows/            # CI
```

## Core Surfaces

- **Adapters** (`infrahub_sync/adapters/`): per-system connectors. Use existing ones as patterns.
- **Engine** (`infrahub_sync/potenda/`): orchestrates `list`, `diff`, `generate`, and `sync`.
- **Examples** (`examples/`): runnable configs and templates.

**CLI commands:**

- `infrahub-sync list` — show available sync projects.
- `infrahub-sync diff` — compute differences (safe).
- `infrahub-sync generate` — generate Python from YAML config (servers required).
- `infrahub-sync sync` — perform synchronization (servers and approval required).

## Configuration and Examples

- YAML config keys: `name`, `source`, `destination`, `order`.
- `source` and `destination` specify adapter names and connection settings.
- `order` defines the sync sequence of object types.
- Defaults often target `localhost`; adjust for real deployments.
- Credentials must come from environment or a secret manager. Never commit secrets.

## Code Standards

### Python (3.10–3.12)

- Prefer explicit types on new or changed code.
- Ruff: formatted and lint-clean. Honor `pyproject.toml`.
- Pylint: fix actionable issues in touched code; some warnings are expected.
- Mypy: run with `--ignore-missing-imports`; do not increase the error count.
- Public functions and classes require concise docstrings.
- Raise specific exceptions; avoid broad `except Exception:`.

### CLI and UX

- Predictable, idempotent commands with clear validation and errors.
- No secrets in logs or tracebacks.
- Prefer explicit flags over implicit behavior.

## Testing

Current state: `tests/` exists but has no active tests.

If you introduce features or bug fixes, add targeted tests.

- Unit tests for `utils` and adapter edge cases (timeouts, 401/403, empty pages).
- Parametrized tests for config parsing.
- Mark network or integration tests and keep them opt-in (for example, `-m integration`).
- Keep tests atomic and single-purpose. Use parametrization rather than loops.

Run:

```bash
poetry run pytest -q
```

## Documentation

- Update `docs/` for any user-visible changes (flags, config, adapters).
- Generate CLI docs:

```bash
poetry run invoke docs.generate
```

- Build site (ensure `cd docs && npm install` once):

```bash
poetry run invoke docs.docusaurus
```

- Keep examples minimal, accurate, and redacted.

### Linting documentation (markdownlint)

Use `markdownlint` and `markdownlint-cli` for Markdown and MDX files.

```bash
# Check and fix Markdown and MDX in docs
npx markdownlint-cli "docs/docs/**/*.{md,mdx}"
npx markdownlint-cli --fix "docs/docs/**/*.{md,mdx}"
```

## Invoke Tasks (reference)

```bash
poetry run invoke --list
# linter.format-ruff     Format Python code with ruff
# linter.lint-ruff       Lint Python code with ruff
# linter.lint-pylint     Lint Python code with pylint
# linter.lint-yaml       Lint YAML files with yamllint
# docs.generate          Generate CLI documentation
# docs.docusaurus        Build documentation website
# format                 Alias for ruff format (if defined)
# lint                   Run all linters
```

## Known Issues and Limitations

- Optional dependencies (for example, `pynetbox`, `pynautobot`) may be missing, producing import warnings.
- `generate` and `sync` require running servers (Infrahub, NetBox, Nautobot).
- Existing mypy debt exists; do not increase it and type the code you touch.
- Docs npm audit may flag dev-only vulnerabilities; they do not affect the Python package.

## Development Rules

### Git and CI

- Do not force-push on shared branches.
- Do not amend to hide pre-commit fixes; use a follow-up commit.
- Apply PR labels: `bugs`, `breaking`, `enhancements`, `features` (default to `enhancements`).
- Always run the required workflow (format → lint → mypy → CLI sanity) before a PR.

### Commit and PR Messages

- Agents must identify themselves (for example, `🤖 Generated with Copilot`).
- Commit subject: imperative “what changed.” Rationale goes in the PR body.
- PR body includes:
    - Problem or tension and the solution in one to two short paragraphs.
    - Minimal code example or before/after snippet.
    - Note any user-visible changes (CLI flags, config keys).

## Review Process

- Read surrounding code and examples. Align with established patterns.
- Verify claims via the smallest reproduction (CLI or unit).
- Consider edge cases: auth failures, empty inputs, pagination, rate limits, timeouts.
- Provide specific, actionable feedback.

**Approval checklist:**

- [ ] Format and lint clean on changed areas.
- [ ] No increase in mypy errors; new code typed.
- [ ] CLI behaviors validated (`--help`, `list`, targeted `generate`).
- [ ] Docs updated if flags or config changed.
- [ ] Error handling uses specific exception types and clear messages.

## Operational and Safety Guidelines

- Prefer dry runs (`diff`, `list`, `generate`) and include outputs in PRs when helpful.
- Least privilege: only touch minimal required resources.
- Idempotency: ensure safe re-runs and guard against partial failures.
- Observability: contextual logging without secrets (request IDs, endpoints, object counts).
- Concurrency: avoid collisions with live migrations or active syncs. Coordinate via PRs.

If unsure, stop and ask with a concrete question.

## Security and Secrets

- Configure credentials via environment variables or secret managers.
- Never print tokens or keys in logs, exceptions, or PRs. Redact examples and tests.
- Keep example configs authentic but sanitized.

## Platform-Specific Notes

Mirror these principles to:

- `CLAUDE.md`
- `.github/copilot-instructions.md`
- `GEMINI.md`
- `GPT.md`
- `.cursor/rules/dev-standard.mdc`

Each should include the “Required Development Workflow” block and the “Approval checklist” verbatim.

## Quickstart

```bash
# Setup
pyenv local 3.12.x || use system Python 3.9–3.12
pip install poetry
poetry install

# Validate dev environment
poetry run infrahub-sync --help
poetry run infrahub-sync list --directory examples/

# Make a change, then:
poetry run invoke format
poetry run invoke lint
poetry run mypy infrahub_sync/ --ignore-missing-imports
poetry run infrahub-sync list --directory examples/

# If docs/CLI changed:
poetry run invoke docs.generate
poetry run invoke docs.docusaurus
```

## Adding a New Adapter

1. Create `infrahub_sync/adapters/<name>.py` following existing adapter patterns.
2. Add connection config schema and an example under `examples/`.
3. Provide `list` and `diff` pathways before enabling `sync`.
4. Document required environment variables and expected error cases.
5. Create a documentation page for the adapter in `docs/docs/adapters/`.
   - Include overview, configuration keys, environment variables, example YAML, and common errors.
   - Add it to the sidebar or navigation as needed.
   - Validate with markdownlint:

   ```bash
   npx markdownlint-cli "docs/docs/adapters/**/*.{md,mdx}"
   npx markdownlint-cli --fix "docs/docs/adapters/**/*.{md,mdx}"
   ```
