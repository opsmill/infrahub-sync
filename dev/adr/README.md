# Decision Records (ADRs)

MADR-format decision records. Name each file `nnnn-title.md`: a zero-padded 4-digit
sequence and a lowercase kebab-case title, no `adr-` prefix (e.g. `0001-use-uv.md`).

## Current

- [0001 — Translate run failures only at the remote boundary](0001-translate-run-failures-only-at-the-remote-boundary.md)
  — how the shared execution surface gains typed sanitized failures without changing CLI
  behaviour.
- [0002 — Resolve remote configurations file by file](0002-resolve-remote-configurations-file-by-file.md)
  — why the remote surface walks the configuration directory tolerantly instead of reusing
  the eager CLI lookup.
- [0003 — A canonical plan fingerprint is the equivalence oracle between run paths](0003-canonical-plan-fingerprint-as-equivalence-oracle.md)
  — how "the remote path produced the same plan" becomes a testable claim.
- [0004 — Declare redis directly instead of using the diffsync `[redis]` extra](0004-declare-redis-directly-instead-of-the-diffsync-extra.md)
  — the dependency conflict that forced a base declaration change, and why the floor is
  permissive.
- [0005 — Optional integrations live in their own package and are proven absent in CI](0005-optional-integrations-live-in-their-own-package.md)
  — the import boundary for an optional integration and the three mechanisms that enforce it.

## Related

- [Knowledge](../knowledge/README.md) — how the system works after these decisions.
- [Guidelines](../guidelines/README.md) — the rules they imply.
- [Constitution](../constitution.md) — the principles they serve.
