# Implementation Plan: Webex source adapter

**Branch**: `016-webex-adapter` | **Date**: 2026-11-06 | **Spec**: [spec.md](spec.md)

## Summary

Add a configuration-driven Webex source by subclassing the generic REST adapter. Reuse existing field/peer conversion and shared filters/transforms; implement Webex-specific GET transport, pagination, OAuth refresh, nested resources and details in the new adapter files. Ship one meeting-room example. Existing source files and CLI behavior remain unchanged.

## Technical Context

**Language/Version**: Python 3.10–3.13.
**Primary Dependencies**: existing requests, pydantic, structlog, Jinja2, diffsync and infrahub-sdk; no additions.
**Storage**: existing DiffSync stores, plus raw parent records retained per adapter run.
**Testing**: pytest with invented JSON fixtures, mocked HTTP and an in-memory destination with populated site IDs; no live services.
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

### Room parents

Map LocationSite with identifier webex_location_id sourced from location id. Its `read_only: true` Webex resource option assigns the existing DiffSync IGNORE model flag, keeping sites in the source store for references without site diff operations. Map room parent from scalar locationId, which the existing generic converter treats as a peer unique key. The destination uses the same key for an existing site's node ID. Fixture success cases require populated, unique destination site IDs.

### Existing destination limitation

Optional Text site identifiers may be None or blank. Existing Infrahub loading can create duplicate/empty identities; existing continue-on-error handles some validation and missing-key errors but does not reliably skip optional empty identifiers. The diff command does not expose that option. This remains a documented follow-up; no destination adapter, shared engine or CLI change is included.

### Destination preparation and write policy

A separate workflow must write Webex location IDs onto matching existing sites before running the example. No enrichment configuration or matching rule is included. Source site models are ignored for diff operations, and the existing SKIP_UNMATCHED_DST flag preserves destination-only sites and rooms. The full displayName identifies rooms, so renames create new rooms while old rooms remain.

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
docs/docs/adapters/webex.mdx
docs/sidebars.ts
changelog/+webex-adapter.added.md
```

The only managed context change is the existing Spec Kit plan-reference line in CLAUDE.md.

## Execution and Validation

1. Validate typed connection/resource settings and environment references.
2. Implement and test bounded GET transport, pagination and service-app refresh.
3. Implement and test configurable extraction, parents, details and reference-only resources using existing core operations.
4. Generate the meeting-room example models from a fixture schema; test filtering, parent resolution, site protection and convergence with populated destination IDs.
5. Document settings, prerequisites and follow-ups; add the news fragment.
6. Run formatting, tests, Ruff, yamllint, ty, new-file Pylint and docs build; verify byte identity of restored files and an empty core diff from 53290877.
7. Replace the two previous feature commits with one commit directly on 53290877 using the requested author/committer; do not push or open a PR.

## Follow-ups

Empty destination site identifiers, cross-side parent preflight, room rename/removal reconciliation and durable OAuth rotation belong to separate changes. No behavior is added to the core to resolve them here.
