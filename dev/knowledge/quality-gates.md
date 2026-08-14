# Quality gates

> Part of: `dev/knowledge/` | Related: [Testing](../guidelines/testing.md)

<!-- Extracted from dev/specs/archive/001-prefect-managed-remote-run on 2026-07-31 -->

What `invoke format` and `invoke lint` actually run, in what order, and what a passing result
does and does not mean. Read this before treating either command as a gate: `invoke lint`
does **not** exit 0 on a clean checkout, and `invoke format` damages tracked Markdown.

## What the aggregates run

`invoke lint` (`tasks/__init__.py`) calls `docs.lint` and then `linter.lint_all`:

| Order | Leg | Command |
|---|---|---|
| 1 | `docs.rumdl` | `rumdl check .` |
| 2 | `linter.lint-ruff` | `ruff format --check --diff . && ruff check --diff .` |
| 3 | `linter.lint-pylint` | `pylint infrahub_sync/` |
| 4 | `linter.lint-yaml` | `yamllint .` |
| 5 | `linter.lint-ty` | Profile-aware `uv run ty check` (see [CI](#ci)) |

**rumdl runs first, and the chain short-circuits.** Every leg calls `context.run` without
`warn=True`, so the first non-zero exit raises and no later leg runs. A Markdown nit
therefore hides every Python finding behind it. If you need a specific leg's status, invoke
that leg directly.

`invoke format` calls `docs.format` (`rumdl fmt .`) and then `linter.format_all`
(`ruff format .` then `ruff check --fix .`).

## `rumdl fmt` loses text in tracked Markdown

`rumdl fmt .` misparses some wrapped lines in this repository's Markdown as ATX headings,
drops text, and cascades a heading demotion through the rest of the file.

`dev/specs/**` — the archived spec artifacts, a historical record — is in the
`[tool.rumdl] exclude` list in `pyproject.toml` for exactly this reason, so `invoke format`
cannot rewrite it. That exclusion covers `rumdl check` too: the archive is not linted, and
is not expected to be. **Every other tracked Markdown file is still in scope for the
formatter**, and the corruption is a property of the tool, not of `dev/specs`. Prefer
`invoke linter.format` when you only mean to format Python.

Use `rumdl check .` and fix violations by hand. When you only want the Python formatters, run
`invoke linter.format` — which is the *formatter* aggregate (`ruff format` + `ruff check
--fix`), despite the name suggesting otherwise.

## There is no `linter.lint` task

`tasks/linter.py` decorates the Python-lint aggregate `lint_all` with `@task(name="format")`,
colliding with `format_all` below it. `invoke --list` shows `linter.format` (which resolves
to `format_all`, the formatter) and **no** `linter.lint`. The Python-lint aggregate is
unreachable by any name; run the four `linter.lint-*` tasks individually.

## The inherited pylint baseline

`pylint infrahub_sync/` does not pass, and has not for a long time. Measured directly on
this repository at commit `697b2f4`, using Python 3.13.3, Pylint 4.0.5, and an environment
synced with `--extra dev --extra prefect --extra managed`:

- exit code **28**, which is pylint's bitmask for warning (4) + refactor (8) + convention
  (16) — not a count;
- rating **9.94/10**;
- **30** diagnostics across **11** message codes.

| Code | Count |
|---|---|
| `C0415` import-outside-toplevel | 5 |
| `C0413` wrong-import-position | 9 |
| `R0917` too-many-positional-arguments | 5 |
| `W0613` unused-argument | 4 |
| `C0302`, `C0412`, `R0912`, `R0915`, `R1705`, `R1720`, `W0707` | 1 each |

**The count is environment-dependent.** pylint can analyse more code once optional
dependencies resolve, so a base-only environment reports fewer diagnostics than a
`dev + prefect` one at the same commit. A total on its own is not a comparable figure.

Treat the **code set** as the baseline, not the number. A no-regression claim means: no code
outside the set above, and no new occurrence attributable to your change.

## Measuring a no-regression claim

Two mistakes cost real time on this repository, both worth avoiding by rule:

- **Read the command's own exit code.** `invoke lint | tail` reports `tail`'s status, which
  is always 0. That is how "`invoke lint` exits 0" came to be believed. Capture the status
  from the command itself, and record the command plus its verbatim output as the evidence.
- **Compare against an extraction of the base commit, not against your own tree.** Extract
  it read-only:

  ```bash
  mkdir -p /tmp/base && git archive <base-sha> | tar -x -C /tmp/base
  ```

  Do not use `git stash` and do not switch branches to measure a baseline. Both mutate the
  working tree you are mid-change in.

  When comparing generated or duplicated file trees, do not flatten them — this repository
  has repeated basenames across adapter directories (`infrahub/sync_adapter.py`,
  `netbox/sync_adapter.py`), and flattening makes the collision look like a diff.

## `infrahub-sync generate` dirties the tree

`infrahub-sync generate --name from-netbox --directory examples/` is prescribed as a CLI
sanity check, and it **rewrites committed files**. The generator templates iterate the live
schema unsorted, so output tracks API response order: expect a few hundred lines of pure
ordering churn, and a fixed point that is stable within a session but moves between sessions.

The expected outcome is a dirty tree to restore with `git checkout -- <paths>`, not a clean
one. The non-determinism itself is a filed bug, not a property to design around.

## CI

Python 3.11–3.13 linting runs in an environment synced with
`--extra dev --extra prefect --extra managed`. The type gate checks the full tree; the
managed extra requires read access to the private `opsmill/prefect-extras` Git dependency.

Python 3.10 linting uses `--extra dev --extra prefect` and runs:

```bash
uv run ty check --exclude infrahub_sync/managed --exclude tests/managed .
```

Managed Sync supports Python 3.11–3.13 only, so this exclusion is the supported direct
Prefect profile rather than a reduced full-service check. `invoke linter.lint-ty` selects
the same command from the active Python version.

Tests run in **two** legs — one with the `prefect` extra, one without, where the base leg
first asserts Prefect is genuinely not importable. See
[ADR 9](../adr/0009-optional-integrations-live-in-their-own-package.md).

Installing the extra has one visible side effect on the type gate: it pulls transitive
packages that resolve imports the base install cannot, so a `# ty: ignore[unresolved-import]`
that is necessary without the extra can be reported as an unused ignore with it.

## See also

- [Testing](../guidelines/testing.md) — what tests must assert before a gate means anything.
- `AGENTS.md` — the required development workflow and approval checklist.
