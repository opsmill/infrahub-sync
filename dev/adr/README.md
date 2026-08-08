# Decision Records (ADRs)

MADR-format decision records. Name each file `nnnn-title.md`: a zero-padded 4-digit
sequence and a lowercase kebab-case title, no `adr-` prefix (e.g. `0001-use-uv.md`).

## Current records

- [0001 — Saved plan artifact format](0001-saved-plan-artifact-format.md) — the manifest and
  canonical-JSON operations a run records, the commit point, the checksum, derived operation
  identifiers, and recursive peer references.
- [0002 — The destination planned-write surface is a Protocol](0002-planned-write-destination-protocol.md)
  — the typed boundary a saved-plan apply writes through, and the member-presence-only limit of
  the runtime check.
- [0003 — The replace-set flush is a targeted relationship write](0003-replace-set-flush-is-a-targeted-relationship-write.md)
  — why no whole-node re-render can flush a reconciled peer set, and the live test that pins the
  destination's replace semantics.
- [0004 — Deletes are recorded but never executed](0004-deletes-are-recorded-but-never-executed.md)
  — the delete contract and the knowability invariant that backs it.
- [0005 — Translate run failures only at the remote boundary](0005-translate-run-failures-only-at-the-remote-boundary.md)
  — how the shared execution surface gains typed sanitized failures without changing CLI
  behaviour.
- [0006 — Resolve remote configurations file by file](0006-resolve-remote-configurations-file-by-file.md)
  — why the remote surface walks the configuration directory tolerantly instead of reusing
  the eager CLI lookup.
- [0007 — A canonical plan fingerprint is the equivalence oracle between run paths](0007-canonical-plan-fingerprint-as-equivalence-oracle.md)
  — how "the remote path produced the same plan" becomes a testable claim.
- [0008 — Declare redis directly instead of using the diffsync `[redis]` extra](0008-declare-redis-directly-instead-of-the-diffsync-extra.md)
  — the dependency conflict that forced a base declaration change, and why the floor is
  permissive.
- [0009 — Optional integrations live in their own package and are proven absent in CI](0009-optional-integrations-live-in-their-own-package.md)
  — the import boundary for an optional integration and the three mechanisms that enforce it.

## Related

- [Knowledge](../knowledge/README.md) — how the system works after these decisions.
- [Guidelines](../guidelines/README.md) — the rules they imply for new code.
- [Constitution](../constitution.md) — the principles they serve.
