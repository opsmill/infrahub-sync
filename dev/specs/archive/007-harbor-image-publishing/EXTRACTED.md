# Extraction Record

**Extracted on**: 2026-10-01
**Extracted by**: speckit.opsmill.extract

## ADRs Created

- `dev/adr/0015-images-publish-to-harbor-through-one-reusable-workflow.md` (from R1, R2, R5, R10)
- `dev/adr/0016-the-compose-deployment-is-one-tag-pinned-file.md` (from R3, R6, R7, and the spec.md clarifications)

## Knowledge Created

- `docs/docs/develop/knowledge/image-publishing.md` (new) — push and pull addresses, the
  reusable workflow's jobs and guards, its callers, the release path and `latest` rule, and the
  residual scan findings.

## Knowledge Updated

- `docs/docs/develop/knowledge/index.md` (Repository workflow)
- `dev/adr/README.md` and `docs/docs/develop/adr-index.mdx` (ADR index)
- `docs/sidebars.ts` (Knowledge and Guidelines entries)

## Guidelines Created

- `docs/docs/develop/guidelines/ci-workflows.md` (new) — shell strictness, inputs and secrets,
  pinning and concurrency, and how workflow behaviour is tested.

## Guidelines Updated

- `docs/docs/develop/guidelines/index.md` (Repository-wide)

## Also corrected

- `docs/docs/develop/guides/publishing-an-image.md` and `quickstart.md` §4: the dispatch
  example now tags the `HARBOR_HOST` name. The first test dispatch (run 36917930677) showed
  that a push addressed to `registry.opsmill.io` fails with 401.

## Archive

The spec directory moved to `dev/specs/archive/007-harbor-image-publishing/` as a historical
record.
