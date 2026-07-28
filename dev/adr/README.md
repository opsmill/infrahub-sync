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
  — why no whole-node re-render can flush a reconciled peer set.
- [0004 — Deletes are recorded but never executed](0004-deletes-are-recorded-but-never-executed.md)
  — the delete contract and the knowability invariant that backs it.

## Related

- [Knowledge](../knowledge/README.md) — how the system works after these decisions.
- [Guidelines](../guidelines/README.md) — the rules they imply for new code.
- [Constitution](../constitution.md) — the principles they serve.
