---
title: "NetBox benchmark tiers"
---

## NetBox benchmark tiers

> Part of: Develop > Guides | Related: [Local development stack](../../development-stack.mdx), [Testing tiers](../guidelines/testing-tiers.md)

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
`[prefix, ip_namespace]` identifier, so a sync sees a delete and a create. The
benchmark runner must translate these prefix moves through the mapping before comparing
the expected file with applied sync actions. IP updates change the mapped description.
The pinned NetBox interface response contains `count_ipaddresses`, without an
`ip_addresses` list, so the source adapter projects that relationship as empty.
IP deletion therefore does not also update an interface relationship.

A marker in an existing tag's unmapped slug blocks a second mutation without adding a
mapped tag. Restore before another run; `--force` reapplies the same updates and leaves
already completed creates and deletes in place; its expected file describes the intended
mutations, rather than new work on that rerun. A failed mutation leaves a `.partial.json`
file and the marker, so restore the dump before retrying. The benchmark runner is a
separate change.
