# 1. Translate run failures only at the remote boundary

**Status**: Accepted
**Date**: 2026-07-31
**Source**: `dev/specs/archive/001-prefect-managed-remote-run/critiques/collation-r1.md` (D009),
`contracts/execution-surface.md`

## Context

The `diff` and serial-`sync` lifecycles had to become callable from something other than
the CLI, without changing anything the CLI does. Two requirements pulled in opposite
directions: a remote caller needs typed, sanitized, self-describing failures, while the
CLI's existing failure behaviour — exit codes, wording, tracebacks — had to stay identical.

The CLI has three distinct failure behaviours at three sites, and two of them are keyed on
the same exception type:

- engine construction raising `ValueError` produces a prefixed abort
  (`Failed to initialize the Sync Instance: …`) with exit code 1;
- a load failure during serial sync produces an *unprefixed* abort — also from a
  `ValueError`, because `Potenda` wraps every load failure into one;
- everything else is marked failed in `run.json` and re-raised unchanged, surfacing as an
  uncaught traceback of the original type.

A single translation layer keyed on exception type cannot reproduce that.

## Decision

`execute_run` raises original exception types and never wraps. Its only surface-typed
raise is its own argument validation, which no CLI caller can reach. Translation into
`RunValidationError` / `RunExecutionError`, and the secret redaction that goes with it,
happens in exactly one function: `run_remote_request`, the remote-only composition.

The CLI keeps its narrow handlers at their original sites by injecting them:

- engine construction goes through a `potenda_factory` the CLI supplies, whose
  `except ValueError` performs the prefixed abort at the construction site;
- the serial-sync load handler calls a CLI-supplied `_serial_load_error` callable, so the
  unprefixed abort still fires where it always did;
- the broad mark-failed-and-re-raise handler around the lifecycle is preserved verbatim,
  documented at the site as the pre-existing pattern rather than as new looseness.

Operator hints that only make sense remotely are added at the remote boundary too. When a
run fails because an adapter's credentials are missing, `run_remote_request` names the
environment variables to set — by name only, attributed to the failing adapter, and
omitted entirely for an adapter with no known variables. The adapter modules are not
touched, so CLI wording stays byte-identical.

## Consequences

CLI identity holds by construction rather than by reconstruction. There is no mapping
table to keep in step, and a CLI regression would require someone to move a handler rather
than to mis-key a translation.

The cost is two private seams in `execute_run`'s signature that exist for one caller each,
and a broad `except` at two sites for different reasons — one preserving history, one
performing translation. Their lint suppressions differ accordingly, and the rule is to
match what ruff actually reports rather than to be uniform:

| Handler as written | `BLE001` | Directive |
|---|---|---|
| blind `except` + bare `raise` of the caught exception | does not fire | none — adding one trips `RUF100` |
| blind `except` + typed re-raise with a sanitized or suppressed cause | fires | targeted `# noqa: BLE001` |

The one form that is `BLE001`-clean, a plain `raise … from exc`, is exactly what the
whole-cause-chain redaction rule forbids: a traceback renders every link, so an unredacted
cause leaks. See [Secret redaction](../guidelines/secret-redaction.md).

Anything escaping configuration resolution other than a `RunValidationError` is wrapped
here as well — the resolution call sits inside the boundary's `try` — because the one
unanticipated exception would otherwise bypass the entire contract.

## Alternatives Considered

Wrap inside `execute_run` and add a CLI layer that unwraps back into the three original
behaviours. Rejected: it has to reproduce three behaviours from metadata, needs a
stage-typed attribute to tell a construction `ValueError` from a load `ValueError`, and
rewrites the tracebacks and wording it exists to preserve.
