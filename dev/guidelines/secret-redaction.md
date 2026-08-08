# Secret redaction

> Part of: `dev/guidelines/` | Related: [The shared execution surface](../knowledge/execution-surface.md), [Writing an adapter](writing-an-adapter.md)

<!-- Extracted from dev/specs/archive/001-prefect-managed-remote-run on 2026-07-31 -->

Rules for any code path that renders a failure across a process boundary — a served
deployment, an API response, a queued job. The adapter rule ("never log a secret") is about
what you write on purpose; this document is about what leaks by accident, in a message you
did not compose, from a library you do not control.

The reference implementation is `collect_secret_values`, `redact` and
`sanitize_exception_chain` in `infrahub_sync/execution.py`. Reuse them rather than writing a
second collector.

## Redact at the boundary, not at the source

**Sanitize where the message leaves the process, not where the exception is raised.**

An adapter's own exception text is fine locally — the CLI operator already has the
credentials. Rewriting adapter messages to be remote-safe changes CLI behaviour and spreads
the obligation across every module. Keep it in one function, at the boundary, and leave the
adapters alone. See
[ADR 5](../adr/0005-translate-run-failures-only-at-the-remote-boundary.md).

## Redact the whole cause chain

**A traceback renders every link, so redacting the wrapper message is not enough.**

```python
# ❌ Bad — the wrapper is clean, the rendered traceback still prints the raw cause
raise RunExecutionError(redact(msg, secrets)) from exc

# ✅ Good — the cause is rebuilt as a sanitized copy
raise RunExecutionError(redact(msg, secrets)) from sanitize_exception_chain(exc, secrets)
```

The alternative is to suppress the context (`__suppress_context__`) with the redacted cause
text inlined into the wrapper message. Either is acceptable; a plain `raise … from exc` is
not.

The binding property to test: a full `traceback.format_exception(...)` rendering of the raised
error contains no unredacted original message *anywhere* in the chain.

Both permitted forms make ruff report `BLE001` at the broad `except`, and the one form that
does not is the leaky one. A targeted `# noqa: BLE001` with a comment naming the reason is
correct there.

## Collect from the environment by name shape — and from every value's URL userinfo

**Credentials arrive in endpoint variables, not only in credential-named ones.**

This is the mistake worth internalising. `NETBOX_ADDRESS`, `PROM_URL` and `CISCO_APIC_URL`
are how adapters learn where to connect. Their names are not credential-shaped. A password
embedded as URL userinfo in one of them reaches a remote caller verbatim in the very first
connection-refused message.

So the environment half of the collector does two passes:

- variables whose **name** is credential-shaped contribute their whole value;
- **every** variable, regardless of name, contributes the userinfo of any URL-shaped value.

A name is credential-shaped when it *contains* `TOKEN`, `PASSWORD`, `PASSWD`, `SECRET`,
`CREDENTIAL` or `APIKEY`, *ends with* `_KEY` or `_AUTH`, or *equals* `KEY`, `AUTH` or
`INFRAHUB_API_TOKEN`.

## Match key names at a boundary, never as bare substrings

**An over-broad match shreds the diagnostics the boundary exists to preserve.**

`KEY` and `AUTH` are matched by suffix or exact name, not as substrings, because the bare
forms sweep in unrelated variables (`KEYCHAIN`, `SSH_AUTH_SOCK`) and — worse — shipped
non-secret configuration keys:

```yaml
response_key_pattern: "objects"     # bare `key` match → every "objects" becomes ***
auth_method: "api-key"              # bare `auth` match → "api-key" becomes ***
```

The result is `Authentication method '***' requires a valid API token!` — a diagnostic
rendered useless by the very mechanism meant to make it safe to show. Over-collection is not
the safe direction; it is a different failure.

The same three boundary rules apply to configuration settings keys, lowercase: *contains*
`token`, `password`, `passwd`, `secret`, `credential`, `apikey` or `authorization`, *ends
with* `_key` or `_auth`, or *equals* `key` or `auth`.

## Walk settings recursively, with a cycle guard and a depth cap

**Secrets nest, and YAML can be self-referential.**

Walk `source`, `destination` **and** `store` settings recursively — a nested
`headers.authorization`, `params.api_key`, or `store.settings.password` is as much a
credential as a top-level one. Along the way:

- **Inherit the qualifying context.** Everything nested beneath a matched key is a candidate,
  which is what a `credentials:` block of plainly-named entries requires — and is also why
  narrow matching above is load-bearing: one over-broad match turns every ordinary word
  beneath it into a redaction target.
- **Guard cycles and cap depth.** `yaml.safe_load` builds self-referential structures from
  aliases (`token: &A\n  nested: *A`). An unbounded walk turns that into a `RecursionError`
  that fails *every* run of that configuration, with nothing pointing at the config's shape
  as the cause. Guard on `(id(container), context)` and stop descending at a fixed depth
  (currently 64) rather than raising.
- **Coerce non-string scalars** (`int`, `float`, `Decimal`) so a numeric credential is still
  collected, and never call `str()` on an arbitrary object — one with a raising `__str__`
  must not be able to break the collector.
- **Follow `*_env_vars` indirection.** A `token_env_vars` or `password_env_vars` list names
  variables the adapter reads instead of an inline value. Collect the **values** those names
  point at, never the names.

## Drop values below a length floor

**A short value turns redaction into a substring shredder.**

`SKIP_TOKEN=1` qualifies by name and contributes the value `1`, after which every `1` in
every message becomes `***` — observed as `within 6***.0 seconds`. Values shorter than 6
characters are dropped. No real credential is that short, so dropping them cannot hide one.

Replace longest values first, so a secret that contains another collected value is fully
covered.

## Never chain a validation library's raw detail

**pydantic's `input_value` echo can render the file it failed on.**

A configuration parse failure must name the logical name and, at most, the file path. Do not
chain the original error verbatim: the echoed input can carry the file's contents, including
inline credentials no collector ever saw.

## Anti-patterns

| Anti-pattern | Do instead |
|---|---|
| `raise Wrapped(redact(msg)) from exc` | Sanitize or suppress the cause too |
| Collecting only from keys named like credentials | Also scan every value's URL userinfo |
| Bare `key` / `auth` substring matching | Suffix or exact-name matching |
| Top-level-only settings scan | Recursive walk with cycle guard and depth cap |
| No length floor on collected values | Drop values under 6 characters |
| Chaining a pydantic `ValidationError` outward | Name the logical name and file path only |
| Rewriting adapter messages to be remote-safe | Redact once, at the boundary |

## Verifying it

Seed canary credential values into the environment and the configuration, drive a failing
run, and scan everything the remote caller can see — parameters, results, logs, state
messages — for the canary values. A test that asserts a *specific* message is redacted proves
much less than a scan that asserts the canary appears nowhere.

Because the collector is security-relevant, a change that *widens* it is owed a review over
its own diff. The suite it was written against will pass either way: both a missed endpoint
variable and an over-collecting substring match were introduced by a remediation whose tests
were all green. See [Testing](testing.md).

## See also

- [The shared execution surface](../knowledge/execution-surface.md) — where the boundary is.
- [Writing an adapter](writing-an-adapter.md) — credential handling inside an adapter.
