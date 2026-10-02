---
title: "Developer guides"
---

## Developer guides

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

#### Seed, dump, and change a NetBox tier

Use the disposable NetBox tasks from a development environment with the `dev`, `prefect`,
and `service` extras. `seed` and `restore` replace the entire local NetBox database.
Tier S preserves the qualification dataset. The tiers contain 560, 10,207, and 87,815
mapped NetBox objects respectively, plus known unnamed-device and ungrouped-VLAN skip
cases. Tier L seeding can take hours; large benchmark runs can also take hours.

```bash
uv run invoke netbox.seed --tier S
uv run invoke netbox.dump --tier S
uv run invoke netbox.restore --tier S
uv run invoke netbox.change --tier S
```

Replace `S` with `M` or `L`. `--dataset demo` accepts only the default tier S and loads the
pinned demo instead. Save a dump before changing a tier. Dumps and their image/checksum
sidecars live under `.netbox/dumps/`; restore refuses a missing dump, a different NetBox
image, or a checksum mismatch. Restore reapplies the local development administrator and
token. Keep these generated files private and outside Git.

Before dumping, the task checks every seeded endpoint's count, the mapped versus skipped
row counts, and the change marker. It does not compare every field with the generated
dataset. Seed or restore a tier before dumping if you have edited its contents by hand.

The change script uses environment credentials and fixed names. It writes
`.netbox/changes/<tier>.expected.json` only after every mutation succeeds, with
`format_version`, `tier`, action `counts`, and `changes`. Each change has `action`, `kind`,
`identifier` (a JSON-encoded natural key), and `fields`; relationships use
`{kind, identifier}` references rather than database IDs. The totals are 6, 102, and 878,
rounded with largest remainders in a 70/20/10 update/create/delete split. Creates and deletes
use only leaf kinds; referenced leaves are excluded from deletion and their allocation
moves to available kinds. At least 30% of updates change relationships.

These are direct NetBox mutations. A prefix VRF move changes its mapped
`[prefix, ip_namespace]` identifier, so a sync sees a delete and a create. IP reassignment
and deletion change the owning interface's mapped `ip_addresses` relationship. The
benchmark runner must translate these mutations through the mapping before comparing
the expected file with applied sync actions. A marker in an existing tag's unmapped
slug blocks a second mutation without adding a mapped tag. Restore before another run;
`--force` reapplies the same updates and leaves already completed creates and deletes
in place; its expected file describes the intended mutations, rather than new work on
that rerun. A failed mutation leaves a `.partial.json` file and the marker, so restore the dump
before retrying. The benchmark runner is a separate change.

### Releases

- [Building a private tester packet](building-a-tester-packet.md) — how to assemble the
  archive handed to a Linux amd64 tester (the qualified image, the Compose deployment
  bundle, and an example configuration) and how the packet task checks the three input
  files against the checksums recorded in `candidate-input.json` during the build step,
  then checks the qualified image configuration and the bundle and release identity
  against `qualification.json` from the qualification step.
- [Qualifying an internal candidate](qualifying-an-internal-candidate.md) — how a teammate
  obtains a pre-release candidate from its Actions run and qualifies it on their own host.

### Related

- [Knowledge](../knowledge/index.md) — how the sync engine, service, and adapters work.
- [Repository tour](../knowledge/repository-tour.md) — where to find the code for each part of the system.
- [Guidelines](../guidelines/index.md) — adapter rules, repository-wide testing, and secret redaction.
- [Testing tiers](../guidelines/testing-tiers.md) — which test command to run after a step.
- [Constitution](../constitution.md) — the principles these guides serve.
