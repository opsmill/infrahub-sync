# 4. Declare redis directly instead of using the diffsync `[redis]` extra

**Status**: Accepted
**Date**: 2026-07-31
**Source**: `dev/specs/archive/001-prefect-managed-remote-run/research.md`
(D005, "Gate-ratified resolution")

## Context

Adding Prefect as an optional extra exposed a hard resolver conflict in the base dependency
set. The chain is metadata-real, not resolver-specific:

- the base declared `diffsync[redis]>=2.1,<3.0`, and every `diffsync` 2.x `redis` extra
  caps `redis>=4.3,<5.0`;
- `prefect>=3.6` requires `pydocket` unconditionally, and every `pydocket` release requires
  `redis>=5`.

`redis<5.0` and `redis>=5` cannot coexist, so any modern Prefect pin makes installing the
extra impossible for every user. The base cannot simply drop redis either:
`infrahub_sync/utils.py` imports `diffsync.store.redis.RedisStore` at module top,
unconditionally, so the client must exist in every install even though the store itself is
opt-in at run time (`LocalStore` is the default; `RedisStore` is built only when a
configuration sets `store.type == "redis"`, and no shipped example does).

The `diffsync` cap turned out to be stale rather than real. Its redis store was verified
functionally intact on redis-py 4.6.0, 5.0, 6.4, 7.0 and 8.1.0 — including `diff_from`,
`diff_to` and `sync_from` between two Redis-backed adapters, with the 4.6.0 control and the
8.1.0 results byte-identical. The store touches only `Redis()`, `Redis.from_url()`, and
`ping` / `get` / `set` / `exists` / `delete` / `scan_iter`.

## Decision

The base drops the extra and declares the client itself, with a deliberately permissive
floor:

```toml
"diffsync>=2.1,<3.0",
"redis>=4.3,<9",
```

The optional extra carries a single pin:

```toml
prefect = ["prefect==3.8.1"]
```

The floor is `>=4.3`, not `>=5`. That is the whole point: an existing environment on redis
4.6 stays valid, a downstream consumer that still requires `diffsync[redis]` still
resolves, and the redis 8.x that `pydocket` needs is still admitted. Overriding a
dependency's declared cap is only acceptable because it was measured against real behaviour
first, and the repository carries a redis-store import-compatibility test so the override
is not taken on trust.

The override is permanent, not a wait-for-upstream stopgap: `diffsync` has no 3.x release
and no open change raising the cap.

## Consequences

`pip install -e '.[prefect]'` resolves, and a base install is unaffected — no existing
installation is forced to upgrade anything. The comments in `pyproject.toml` carry the
reason, because the natural reading of `redis` next to `diffsync` is that someone forgot the
extra, and the natural "cleanup" is to restore it and break the extra again.

Two follow-on costs. The repository now owns a compatibility claim about someone else's
optional store, so the import-compatibility test is load-bearing rather than incidental.
And a dependency conflict of this shape is only discoverable by attempting the install
against the real project metadata: an unpinned `--with` overlay resolves happily and proves
nothing, which is how the original pin was believed available.

## Alternatives Considered

**Keep `diffsync[redis]` and pin `prefect<3.6`** — the newest Prefect without `pydocket` is
3.5.0, which turned out to be independently broken out of the box at the time (a dropped
`importlib_metadata` declaration that its worker module still imports, and a `fastapi<1.0`
bound loose enough that a fresh resolve returned HTTP 500 from the deployment routes). Both
needed companion pins in the extra, and both are fixed upstream by 3.8.1. Rejected: pinning
the extra to a version that needs two repair pins to start is worse than adjusting one base
declaration.

**Allow `redis>=5`** — rejected: it ships a redis major bump to every existing user inside a
preview, and it makes a downstream `diffsync[redis]` requirement unsatisfiable.
