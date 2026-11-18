# Implementation Plan: Webex source adapter

**Branch**: `016-webex-adapter` | **Date**: 2026-11-06 | **Spec**: [spec.md](spec.md)

## Summary

Add a configuration-driven Webex source by subclassing the generic REST adapter. Reuse existing field/peer conversion and shared filters/transforms; implement Webex-specific GET transport, pagination, OAuth refresh, nested resources and details in the new adapter files. Ship one three-kind inventory example with an accompanying destination schema. Existing source files and CLI behavior remain unchanged.

## Technical Context

**Language/Version**: Python 3.10–3.13.
**Primary Dependencies**: existing requests, pydantic, structlog, Jinja2, diffsync and infrahub-sdk; no additions.
**Storage**: existing DiffSync stores, plus raw parent records retained per adapter run.
**Testing**: pytest with invented JSON fixtures, mocked HTTP and an in-memory destination with inventory kinds and peer relationships; no live services.
**Target Platform**: existing library and CLI.
**Performance Goals**: complete extraction of 1,000+ fixture records across pages and empty intermediate pages.
**Constraints**: GET data requests; OAuth refresh POST only at `/v1/access_token`; environment-only credentials and safe errors; HTTPS same-origin requests without redirects.
**Scope**: two new source files, Webex tests/fixtures, one example, adapter docs/sidebar entry, news fragment and feature artifacts.

## Constitution Check

- Read-only pathways use the existing engine; no live sync is executed.
- Loading handles pagination, auth, rate limits, timeouts and malformed responses explicitly.
- New code is typed and uses the existing mapping contract and structured logging.
- No dependencies, shared filter operations, existing adapters, CLI flags or global traceback settings change.
- All examples and fixtures use invented data.

## Design

See [research.md](research.md), [data-model.md](data-model.md), [configuration contract](contracts/configuration.md) and [quickstart.md](quickstart.md).

### Inventory identities and peers

Map `/v1/locations`, `/v1/workspaces` and `/v1/devices` to WebexLocation, WebexWorkspace and WebexDevice. Every kind uses webex_id mapped from id as its identifier. Workspaces reference locations through locationId; devices reference workspaces through workspaceId. Explicit order loads and writes locations before workspaces, then devices. Display-name changes update the same nodes.

### Destination preparation and write policy

Load the accompanying example schema before generating the project. It defines the inventory attributes and optional relationships. The example creates all three kinds in an empty destination and uses no reference-only resource or destination-retention flags. Keep the complete location and workspace mappings when importing devices that reference them.

### Existing destination limitation

The Infrahub destination implements create and update but no persisted delete. The removal test proves a one-device delete diff and removal from the in-memory DiffSync store; the Infrahub node remains and would return on the next load. Persisted deletion is a core follow-up.

### Secret handling

Credential-bearing HTTP request and token-parsing frames are confined to client helpers. Their failures are caught and replaced with safe exceptions without retaining those frames in displayed traceback chains. Tests exercise local-variable traceback display while leaving the existing CLI configuration untouched.

## Project Structure

```text
infrahub_sync/adapters/webex.py
infrahub_sync/adapters/webex_client.py
tests/adapters/test_webex.py
tests/adapters/test_webex_client.py
tests/adapters/test_webex_examples.py
tests/fixtures/webex/*.json
examples/webex_workspaces_to_infrahub/config.yml
examples/webex_workspaces_to_infrahub/schema.yml
docs/docs/adapters/webex.mdx
docs/sidebars.ts
changelog/+webex-adapter.added.md
```

## Execution and Validation

1. Validate typed connection/resource settings and environment references.
2. Implement and test bounded GET transport, pagination and service-app refresh.
3. Implement and test configurable extraction, parents, details and reference-only resources using existing core operations.
4. Generate inventory models from the fixture schema; test 25 device creates, mapped attributes, peer write order and convergence after destination reload.
5. Verify one-device delete diffs and separate organization projects; document the persisted-deletion limitation.
6. Document settings and schema prerequisites; add the news fragment.
7. Run formatting, tests, Ruff, yamllint, ty and documentation checks. Leave commit and publication actions to the engineer.

## Follow-ups

Persisted Infrahub deletion, cross-side peer preflight and durable OAuth rotation belong to separate changes. No core behavior is added to resolve them here.
