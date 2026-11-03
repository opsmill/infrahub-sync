# Implementation Plan: Microsoft Entra ID source adapter

**Branch**: `015-entraid-source-adapter` | **Date**: 2026-11-03 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/001-entraid-source-adapter/spec.md`

## Summary

Add a source-only adapter `entraid` that reads users and groups from Microsoft Entra ID through Microsoft Graph and
loads them into DiffSync models for an Infrahub destination. Named, optionally filtered `collections` are each read
once per run and can feed several `schema_mapping` entries. Groups can carry their direct or transitive user members
as a relationship to synced users. The adapter uses `msgraph-sdk` request builders with raw JSON responses, its own
bounded retry loop for HTTP 429 and 503, `azure-identity` async credentials (`client_secret` or `default`), and
`structlog` over the package's standard logger. The work reuses the request, paging and settings logic of the
prototype in `/home/ubuntu/src/entraid-seed/` without its delta, device, static-token and scope features.

## Technical Context

**Language/Version**: Python 3.10 to 3.13 (`ty` checks against 3.10)

**Primary Dependencies**: `diffsync`, `infrahub_sync` mixins, `structlog`, `httpx`; optional and not added to
`pyproject.toml`: `msgraph-sdk` (with `msgraph-core`, `microsoft-kiota-*`) and `azure-identity`

**Storage**: N/A (records held in memory for one run)

**Testing**: `pytest` with `httpx.MockTransport`, a fake async credential and a patched `asyncio.sleep`; modules
guarded by `pytest.importorskip("msgraph")`

**Target Platform**: Linux and macOS command line, CI pipelines

**Project Type**: Library and CLI (adapter plug-in of `infrahub-sync`)

**Performance Goals**: One request series per collection per run; member requests at a fixed concurrency of 4;
peer lookup through one index per referenced kind (no per-record scan of the store)

**Constraints**: No secrets in logs or messages; `graph_url` must be HTTPS; no changes to `pyproject.toml`,
`uv.lock` or the Infrahub adapter; no new ruff, ty, pylint or rumdl findings

**Scale/Scope**: Tenants with tens of thousands of users and thousands of groups (one member request series per
group when members are mapped)

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | How the design complies |
| --- | --- | --- |
| I. Read-only / dry run by default | Pass | Source only. Use as a destination fails at construction; model `create`/`update`/`delete` raise. Only `GET` requests. |
| II. Idempotency and safety | Pass | Full read every run; sorted member lists give stable diffs. 401/403, 429/503, timeouts, empty pages and paging handled explicitly with clear errors. |
| III. Adapter symmetry | Pass | `infrahub_sync/adapters/entraid.py`, mixins first, flows through `potenda`; example under `examples/`, docs page under `docs/docs/adapters/`, env vars and errors documented. |
| IV. Type safety | Pass | Typed dataclasses and functions, `str \| None` syntax, specific exceptions, targeted `# ty: ignore[unresolved-import]` only on optional imports, no `[[tool.ty.overrides]]`. |
| V. Test discipline | Pass | Unit tests per FR-026 with mocked HTTP; parametrized settings validation. |
| VI. Security and secrets | Pass | Secret only from `ENTRAID_CLIENT_SECRET`, `repr=False`, never logged; example uses `example.com` and zero GUIDs. |
| VII. Simplicity | Pass | One module; no new core dependency; three small extra settings (`select`, `timeout`, `max_retries`) only; generated example code produced by the generator templates. |

Post-design re-check: Pass. No violations to justify.

## Project Structure

### Documentation (this feature)

```text
specs/001-entraid-source-adapter/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── config-contract.md
├── checklists/
│   └── requirements.md
└── tasks.md             # created by /speckit-tasks
```

### Source Code (repository root)

```text
infrahub_sync/adapters/
└── entraid.py                     # settings, Graph client, adapter, model base class

tests/adapters/
├── entraid_fakes.py               # mocked Graph transport, fake credential, test models (shared helpers)
├── test_entraid_settings.py       # settings validation, unknown collection, credential construction
├── test_entraid_graph.py          # paging, $select, $filter, advanced query, retries, 401/403, timeout, empty
└── test_entraid_adapter.py        # shared collection, transform, membership, dropped references, destination

examples/entraid_to_infrahub/
├── config.yml
├── schema.yml                     # Infrahub schema of the example kinds
├── entraid/{__init__,sync_adapter,sync_models}.py     # generated
└── infrahub/{__init__,sync_adapter,sync_models}.py    # generated

docs/docs/adapters/entraid.mdx
docs/sidebars.ts                   # add 'adapters/entraid'
changelog/+entraid-adapter.added.md
AGENTS.md, dev/guides/adding-an-adapter.md   # add `entraid` to the built-in adapter lists
```

**Structure Decision**: One adapter module, as for every other built-in adapter. Test helpers live in a module
that is not collected as tests (`entraid_fakes.py`), so the three test files share one mocked Graph.

## Design

### Module layout of `entraid.py`

1. Optional imports (`msgraph`, `msgraph_core`, `kiota_*`, `azure.identity.aio`), each with
   `# ty: ignore[unresolved-import]` and `# pylint: disable=import-error`.
2. Constants: resources, reserved names, `MEMBERS_KEY = "members"`, `USER_ODATA_TYPE`, permission names,
   retry fallback and cap, member concurrency.
3. Exceptions: `EntraidConfigError(ValueError)`, `EntraidGraphError(RuntimeError)`,
   `EntraidPermissionError(EntraidGraphError)`. `ValueError` lets `get_potenda_from_instance` wrap start-up errors
   with the adapter name.
4. `Collection` and `EntraidSettings` dataclasses with `from_settings(raw, environ)` and `resolve_collection(name)`.
5. `build_credential(settings)` → `ClientSecretCredential` or `DefaultAzureCredential`.
6. `EntraidGraphClient` (async context manager): `_send(info, headers)` with the retry loop, timeout and error
   mapping; `_pages(builder, first_info, headers)`; `list_collection`; `user_member_ids`.
7. `EntraidAdapter` and `EntraidModel`.

### Request flow for one run

```text
model_loader(first kind)
  └─ _records() ── asyncio.run(_read_all())
        ├─ for each planned collection: list_collection(collection, select)
        └─ for each groups collection with needs_members:
               gather(user_member_ids(group) for group, at most 4 at once) → record["members"]
model_loader(kind)
  ├─ records = [dict(r) for r in cache[collection]]
  ├─ filter_records → transform_records
  ├─ peer index per referenced kind (built once)
  ├─ record_to_diffsync → self.add(model(**data))
  └─ log loaded/read; one warning if member references were dropped
```

### Error mapping in `_send`

| Response | Action |
| --- | --- |
| 2xx | return parsed JSON (empty body → `{}`) |
| 429, 503, attempts left | log a warning, sleep `min(Retry-After or 5, 120)`, resend |
| 429, 503, no attempts left | `EntraidGraphError` |
| 401, 403 | `EntraidPermissionError` |
| other `APIError` | `EntraidGraphError` with status and Graph error code |
| `httpx.TimeoutException` | `EntraidGraphError` naming the path and timeout |

Error messages carry the URL path and query (no credentials are in URLs), never headers.

## Risks

- **Graph behavior that mocks cannot show** (for example the exact error for an advanced filter sent without the
  header). Mitigation: documentation explains the flag; the error message carries Graph's code and message.
- **CI without the optional packages**: tests skip; the import lines carry `ty` and `pylint` suppressions.
- **`uv sync` removes the optional packages** from `.venv`: implementers reinstall them (see quickstart).
- **Large tenants with members mapped**: one request series per group. Documented as a cost; `$expand` is not an
  option because Graph caps expanded members at 20.

## Complexity Tracking

No constitution violations.
