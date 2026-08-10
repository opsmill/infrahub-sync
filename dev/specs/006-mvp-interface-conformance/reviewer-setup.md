# DB-006 reviewer setup

## Scope and revision

- Repository: `opsmill/infrahub-sync`
- Required base: `8ba0c57a7601bd0ffc75431488d6b59717217de6`
- Review branch: `feature/db-006-mvp-interface-conformance`
- This change adds only opt-in standalone consumption of the existing DB-003
  SQLite/filesystem product projection. It adds no provider, route, operation,
  configuration-package registry, scheduler, recovery surface, or Prefect authority.

## Setup

```bash
git rev-parse HEAD
git merge-base --is-ancestor 8ba0c57a7601bd0ffc75431488d6b59717217de6 HEAD
uv sync --extra dev --extra prefect
```

The managed comparison additionally needs the repository's already-declared `managed`
extra. Do not change the accepted Prefect Extras Git pin.

## Focused review

```bash
uv run pytest -q tests/conformance
uv run pytest -q tests/api/test_v1.py tests/test_execution_cli_parity.py
uv run pytest -q tests/product_store tests/managed tests/orchestration/test_flow.py
uv run ruff check infrahub_sync tests/conformance tests/api/test_v1.py
uv run ty check .
```

Review these seams first:

1. `tests/conformance/oracle.py` — bounded normalization and lossless field retention.
2. `infrahub_sync/product_store/standalone.py` — the opt-in projection adapter.
3. `infrahub_sync/api/v1/_operations.py` — one product identity across composed sync.
4. `infrahub_sync/cli.py` — thin option routing; legacy behavior when the option is absent.

## Manual smoke checks

```bash
uv run infrahub-sync --help
uv run infrahub-sync diff --help
uv run infrahub-sync sync --help
uv run infrahub-sync apply --help
uv run infrahub-sync list --directory examples/
```

`generate --name from-netbox` is integration-backed. Run it only after completing the
documented fresh Infrahub/schema/NetBox-demo setup; otherwise record that external
precondition rather than treating the refusal as a product regression.

## Safety

- `product_cache_location` / `--product-cache-location` is optional and must be absolute
  after `~` expansion.
- Use the same location for plan, verify, and apply.
- Write-capable cases must use isolated destinations and explicit confirmation.
- Do not point a review at a live product cache or shared destination.
