# Guidelines

Prescriptive rules — how adapter code should be written. Guidelines say what you must and
must not do; for how the system works see [`dev/knowledge/`](../knowledge/README.md), and
for step-by-step procedures see [`dev/guides/`](../guides/README.md).

## Adapters

- [Writing an adapter](writing-an-adapter.md) — structure, typing, error handling, logging,
  optional dependencies, and secret handling for a connector.
- [Testing adapters](testing-adapters.md) — the test coverage every adapter must ship and
  the conventions those tests follow.

## Repository-wide

- [Testing](testing.md) — what makes a test worth having: mutation kill as the acceptance
  criterion, asserting a negative, and reviewing a remediation over its own diff.
- [Secret redaction](secret-redaction.md) — rules for any failure path that crosses a
  process boundary: where to sanitize, what to collect, and how over-collection fails.

## Related

- [Knowledge](../knowledge/README.md) — the architecture these rules apply to.
- [Adding an adapter](../guides/adding-an-adapter.md) — the procedure that applies them.
- [Decision records](../adr/README.md) — the decisions behind these rules.
- [Constitution](../constitution.md) — the principles these rules serve.
