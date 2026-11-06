# Quickstart: validate the Entra ID source adapter

## Prerequisites

```bash
uv pip install msgraph-sdk azure-identity   # optional dependencies, not in pyproject.toml
uv run python -c "import msgraph, azure.identity"
```

`uv sync` removes these two packages; reinstall them after any `uv sync`.

## 1. Unit tests (no network, no tenant)

```bash
uv run pytest -q tests/adapters/ -k entraid
uv run pytest -q          # full suite: baseline 210 passed, 3 skipped, plus the new tests
```

Expected: every `test_entraid_*` test passes. Without `msgraph` installed, the files are skipped, not failed.

## 2. Lint

```bash
uv run ruff check infrahub_sync/adapters/entraid.py tests/adapters/
uv run ruff format --check infrahub_sync/adapters/entraid.py tests/adapters/
uv run ty check .
uv run pylint infrahub_sync/adapters/entraid.py
uv run rumdl check docs/docs/adapters/entraid.mdx changelog/
uv run invoke lint        # compare with /home/ubuntu/src/overnight/lint-baseline.txt: no new findings
```

## 3. CLI sanity

```bash
uv run infrahub-sync --help
uv run infrahub-sync list --directory examples/
```

Expected: `list` shows `from-entraid` with source `entraid` and destination `infrahub`.

## 4. Example code matches the generator

Rendering `examples/entraid_to_infrahub/schema.yml` through `infrahub_sync.utils.render_adapter` and
`ruff format` gives the committed `entraid/` and `infrahub/` files (see research R12).

## 5. Against a real tenant (manual, not part of CI)

```bash
export ENTRAID_TENANT_ID=... ENTRAID_CLIENT_ID=... ENTRAID_CLIENT_SECRET=...
uv run infrahub-sync diff --name from-entraid --directory examples/
```

Expected: a diff of users, groups and membership; no write. Run `sync` only with explicit approval.
