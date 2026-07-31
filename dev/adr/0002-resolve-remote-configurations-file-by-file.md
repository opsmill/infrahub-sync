# 2. Resolve remote configurations file by file

**Status**: Accepted
**Date**: 2026-07-31
**Source**: `dev/specs/archive/001-prefect-managed-remote-run/critiques/collation-r1.md` (D010),
`contracts/execution-surface.md`

## Context

`utils.get_all_sync` discovers every `config.yml` under a directory and validates all of
them before any name matching happens. For a CLI invocation that is fine — the operator
sees the error and fixes the file.

A served remote surface resolves a *logical name* against one directory that holds every
installed configuration, and there the eager pass has two problems. One unreadable or
invalid neighbour makes every other name unresolvable, so a single bad file takes the whole
runner offline. And pydantic's validation error echoes `input_value`, so a parse failure can
render the offending file's contents — including inline credentials that no redactor
collected — into a message that travels back to a remote caller.

## Decision

`resolve_sync_instance` performs its own tolerant walk: the same recursive `**/config.yml`
glob and the same exact-string `name` match the CLI lookup uses, but each discovered file is
read and validated individually.

Each discovered file lands in exactly one of three states:

- **determinable** — read and `yaml.safe_load` both succeeded and the document is a
  mapping; the file's name is its top-level `name` value;
- **determinable and different** — skipped silently (a DEBUG line at most), the ordinary
  case for every other configuration in the directory;
- **undeterminable** — the read raised `OSError` or `UnicodeDecodeError`, the parse raised
  `yaml.YAMLError`, or the document is not a mapping; skipped with a WARNING naming the
  *file path only*, counted, and resolution continues.

The matched file is validated as `SyncConfig`, and the resulting `SyncInstance` is built
with `directory` set to that file's own parent — not the configured root — because
`utils.import_adapter` resolves the generated adapter at
`<instance.directory>/<adapter.name>/sync_adapter.py`. If validation fails, the error names
the logical name and the file path and nothing else; the parse detail is never chained
verbatim.

The requested name is never used to build a filesystem path, so traversal-shaped,
absolute, separator-bearing and command-like values all fail exactly like unknown names.
When no file matched, the error names the logical name and, if the walk skipped any
undeterminable files, their **count** — never their paths or contents — so an operator can
tell a typo from a broken configuration.

The CLI keeps calling `utils.get_instance`; its resolution behaviour is unchanged.

## Consequences

The remote failure contract becomes implementable: a broken neighbour cannot block another
name, and a bad-YAML file can never *be* the match, because it has no determinable name to
match with.

There are now two resolution paths over the same directory layout, which must be kept in
step deliberately — the discovery glob and the exact-name rule are the shared contract, and
a change to either belongs in both. The tolerant walk also reads and parses each file
itself rather than reusing the eager helper, so a future change to `SyncConfig` discovery
has two call sites.

## Alternatives Considered

Reuse `get_all_sync` as-is and document the eager behaviour. Rejected: the promised remote
failure shape becomes undeliverable, the blast radius of one bad file stays, and the
content-leak path through pydantic's `input_value` echo stays open.
