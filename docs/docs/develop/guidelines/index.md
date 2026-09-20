---
title: "Guidelines"
---

## Guidelines

Rules for writing and testing Infrahub Sync code, including adapter conventions and
repository-wide testing and secret redaction. For how the system works, see
[Knowledge](../knowledge/index.md); for step-by-step procedures, see [Guides](../guides/index.md).

### Adapters

- [Writing an adapter](writing-an-adapter.md) — structure, typing, error handling, logging,
  optional dependencies, and secret handling for a connector.
- [Testing adapters](testing-adapters.md) — the test coverage every adapter must ship and
  the conventions those tests follow.

### Repository-wide

- [Testing](testing.md) — what makes a test worth having: mutation kill as the acceptance
  criterion, asserting a negative, and reviewing a remediation over its own diff.
- [Testing tiers](testing-tiers.md) — which command runs which tier, what each one needs
  before it proves anything, what it writes, and why a skipped check is not a pass.
- [Secret redaction](secret-redaction.md) — rules for any failure path that crosses a
  process boundary: where to sanitize, what to collect, and how over-collection fails.

### Related

- [Knowledge](../knowledge/index.md) — the architecture these rules apply to.
- [Repository tour](../knowledge/repository-tour.md) — where to find the code for each part of the system.
- [Adding an adapter](../guides/adding-an-adapter.md) — the procedure for implementing and testing a new adapter.
- [Decision records](../adr-index.mdx) — the decisions behind these rules.
- [Constitution](../constitution.md) — the principles these rules serve.
