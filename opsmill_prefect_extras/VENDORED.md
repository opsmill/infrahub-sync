# Vendored: opsmill-prefect-extras

This directory is a **byte-identical** copy of the private upstream package
`opsmill/prefect-extras` at commit `84688eb8d8db7e17770413640a66481ccdc3e725`
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
restore the dependency in the `managed` extra, and drop the vendoring entries
from the Ruff exclude list, `[tool.ty.src]` exclude, the isort
`known-third-party` pin, and `.github/file-filters.yml`.
`tests/test_vendoring_consistency.py` fails on any half-executed re-adoption.
