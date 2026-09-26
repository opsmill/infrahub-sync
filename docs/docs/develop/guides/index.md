---
title: "Guides"
---

## Guides

Step-by-step procedures for adapter development, local service development, and release
qualification. For coding and testing rules, see [Guidelines](../guidelines/index.md); for
how the system works, see [Knowledge](../knowledge/index.md).

### Adapters

- [Adding an adapter](adding-an-adapter.md) — the end-to-end procedure for connecting a new
  source or destination system: the connector, its capability declaration, the conformance
  tests, and the register-to-apply flow.
- [Testing an adapter](testing-an-adapter.md) — how to write and run an adapter's unit and
  integration tests.

### The development environment

- [Local development stack](../../development-stack.mdx) — starting the disposable stack, the
  service development loop, and the destructive reset.

### Releases

- [Building a private tester packet](building-a-tester-packet.md) — how the
  packet task checks recorded inputs and assembles the download for Linux amd64 testers.
- [Qualifying an internal candidate](qualifying-an-internal-candidate.md) — how a teammate
  obtains a pre-release candidate from its Actions run and qualifies it on their own host.

### Related

- [Knowledge](../knowledge/index.md) — how the sync engine, service, and adapters work.
- [Repository tour](../knowledge/repository-tour.md) — where to find the code for each part of the system.
- [Guidelines](../guidelines/index.md) — adapter rules, repository-wide testing, and secret redaction.
- [Testing tiers](../guidelines/testing-tiers.md) — which test command to run after a step.
- [Constitution](../constitution.md) — the principles these guides serve.
