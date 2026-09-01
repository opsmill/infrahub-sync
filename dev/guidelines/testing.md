# Testing

> Part of: `dev/guidelines/` | Related: [Testing adapters](testing-adapters.md), [Quality gates](../knowledge/quality-gates.md)

<!-- Extracted from the archived prefect remote-run spec (dev/specs/archive/001, commit 33817cf) on 2026-07-31 -->

Repository-wide rules for tests. [Testing adapters](testing-adapters.md) covers what an
adapter must ship; this document covers what makes any test worth having.

## A passing test is not evidence — a killed mutation is

**For contract-bearing behaviour, the acceptance criterion is that breaking the code breaks a
test.**

Tests that assert nothing pass. Real examples from this repository, all green before they
were fixed: remote-safety defaults, the depth of cause-chain redaction, longest-secret-first
replacement ordering, the diff rendering itself, the field positions of a contractual log
line, the pinned keyword shape of an injected factory, and an inherited-context flag whose
outright deletion left the entire suite green.

The procedure, when you add or fix a test for behaviour something else depends on:

1. name the mutation — the smallest edit that would make the behaviour wrong;
2. apply it and confirm a test **fails**;
3. revert it.

Adding an assertion and declaring the gap closed is not the same thing, and the difference is
not visible in a coverage report.

## Assert the negative in a fresh process

**An in-process `sys.modules` assertion cannot prove an import did not happen.**

pytest collection imports test modules before your test runs — including ones that import
optional dependencies behind an `importorskip` — and under distributed runs that pollution is
per-worker arbitrary. Build the script, run it in a fresh interpreter, and assert on the exit
code and output:

```python
# ✅ Good — the probe owns its own interpreter
subprocess.run([sys.executable, "-c", script], check=False, capture_output=True)

# ❌ Bad — passes or fails depending on collection order
assert not [m for m in sys.modules if m.startswith("prefect")]
```

Static import-graph checks are collection-safe and can stay in-process.

**Pair an "optional dependency" claim with a CI leg where it is genuinely absent**, and have
that leg *assert the absence* before running the suite — otherwise it silently becomes a
second full-extra run the day the lockfile changes. See
[ADR 9](../adr/0009-optional-integrations-live-in-their-own-package.md).

## Never reimplement a contract algorithm in a test

**Call the shared helper on both sides of a comparison.**

A test that recomputes a fingerprint, a digest, or a canonical form is testing its own copy.
When the definition moves, the copy silently keeps asserting the old contract. Import the
helper.

## Isolate external state completely, and verify the isolation

**Redirect every state location the tool uses, not the one that is documented.**

`PREFECT_HOME` isolates Prefect's database but **not** persisted run results, which follow
`PREFECT_LOCAL_STORAGE_PATH`. Setting one leaves tests writing into the developer's real
directories. When a tool has more than one state root, set them all under `tmp_path` and say
in a comment which one covers what.

The same rule applies to processes and ports: start what you need on a loopback address, and
confirm afterwards that it is gone.

## Mark private test seams as private, and keep production callers off them

**A sanctioned seam is prefixed, documented, and never set by the real caller.**

`execute_run` and `run_remote_request` carry `_`-prefixed parameters — an injectable factory,
a shortened lock timeout, a CLI-only error callback. They exist so a test can drive the real
boundary instead of improvising a monkeypatch target. Each is documented as not part of the
contract, and the remote path never sets one. If a production caller needs a seam, it is not a
seam.

Prefer a `Protocol` over `Callable[..., X]` for an injected callable whose keyword names
matter: the call shape becomes part of the type, so a rename in the real implementation is a
type error rather than a runtime `TypeError` inside the code you were protecting.

## Review a remediation over its own diff

**A fix written against a green suite is validated by nothing.**

Any change that widens a security-relevant collector, crosses a process boundary, or touches
an error path is a new change and is owed a review over *its own* diff. Two credential leaks
and an unbounded recursion in this repository were introduced by a remediation whose tests all
passed, and were found only by a second pass scoped to the remediation diff. See
[Secret redaction](../guidelines/secret-redaction.md).

## Conventions

- Parametrize instead of looping; keep each test atomic and single-purpose.
- Mark network and integration tests opt-in (`-m integration`); they must not run in the
  default suite.
- Run `uv run pytest -q` before committing. `invoke lint` is not a test gate — see
  [Quality gates](../knowledge/quality-gates.md).

## See also

- [Testing adapters](testing-adapters.md) — the per-adapter coverage requirements.
- [Testing an adapter](../guides/testing-an-adapter.md) — how to write and run them.
- [Quality gates](../knowledge/quality-gates.md) — what the lint and format aggregates do.
