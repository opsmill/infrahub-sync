# Vendored: opsmill-prefect-extras

This directory is a **byte-identical** copy of the private upstream package
`opsmill/prefect-extras` at commit `97465e75137f6121d0377cd637383cfb3530d734`
(branch `feature/db-104-executors`; upstream PRs #13–#17 remain open). Its
upstream unit tests are copied, also byte-identical, under
`tests/vendored_prefect_extras/`. Only this file and the test directory's
`__init__.py`/`conftest.py` guard are local additions; no upstream file is
modified.

It is vendored so this repository installs and tests without access to the
private upstream repository. It keeps its original top-level import name so
that consumer imports, tests, and the eventual re-adoption need no rewrites,
and so `diff -r` against the upstream commit stays empty.

Known side effect of the frozen test suite: upstream's
`tests/vendored_prefect_extras/workflows/conftest.py` computes a "repository
root" from its own location and prepends it to `sys.path`; in this repository
that path is the `tests/` directory. It is harmless here (nothing imports the
test packages by their short names) and is left in place rather than edited.

## Freeze rule — do not modify this directory

A change needed here must either:

1. be submitted upstream first and re-copied byte-identically (updating the
   pinned commit recorded above and in `pyproject.toml`), or
2. be explicitly recorded in the planning decision log as ending the
   re-adoption plan.

Local lint/format tooling must exclude this directory rather than rewrite it.

## Re-adoption condition

When upstream `opsmill/prefect-extras` is merged and published, delete this
directory and `tests/vendored_prefect_extras/`, remove
`"opsmill_prefect_extras"` from `[tool.hatch.build.targets.wheel] packages`,
restore the dependency in the `service` extra, and drop the vendoring entries
from the Ruff exclude list, `[tool.ty.src]` exclude, the isort
`known-third-party` pin, and `.github/file-filters.yml`.
`tests/test_vendoring_consistency.py` fails on any half-executed re-adoption.

### CI authentication on re-adoption

Restoring the dependency reintroduces a problem vendoring removed: `opsmill/prefect-extras`
is a private repository, so CI can no longer install the `service` extra with the default
job token.

Two approaches to this were written before vendoring was chosen, and both were dropped
unmerged. They are kept because the second one is worth reusing rather than rediscovering:

* Commit `cadc0ffe` on branch `feature/pr173-ci-auth-rejected` passes the credential to
  `actions/checkout`, which makes it available to the whole clone for the rest of the job.
* Tag `archive/pr173-ci-credential-scoping` replaces that with a git credential helper set
  as `env` on the dependency-install step alone, so no other step can read the token. Run
  `git show archive/pr173-ci-credential-scoping` for the exact change.

Prefer the scoped form. Whichever is used, the workflows are reusable
(`workflow_call`), so the credential must also be declared under their `secrets:` inputs —
an undeclared secret resolves to an empty string and fails as a confusing authentication
error rather than a missing-input error.

Neither branch nor tag is live work. Do not merge them; treat them as a starting point for
the re-adoption change.
