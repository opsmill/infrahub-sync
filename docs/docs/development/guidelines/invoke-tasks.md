---
title: "Invoke task names"
---

## Invoke task names

> Part of: Develop > Guidelines | Related: [Quality gates](../knowledge/quality-gates.md), [Repository tour](../knowledge/repository-tour.md)

Rules for adding, renaming or removing an Invoke task in `tasks/`.

### One name per task

- `format` and `lint` are the only top-level tasks. Every other task belongs to a namespace
  module (`linter`, `docs`, `tests`, `dev`, `netbox`, `bench`, `release`), registered in
  `tasks/__init__.py`.
- A task has exactly one name. Do not add an alias at the top level or in a second namespace.
- `tests/test_task_inventory.py` walks the collection. It fails when a top-level task other
  than `format` and `lint` appears, when a name appears twice, or when a name in its
  `REQUIRED` set is missing. Add a new task that callers depend on to `REQUIRED`.

### Renaming or removing a task

- Move every caller in the same pull request: `.github/` workflows and scripts, `docs/docs/`,
  `AGENTS.md`, `README.md` and `development/README.md`.
- Add the old name to `REMOVED` in `tests/test_task_inventory.py`, and add a pattern for it to
  `REMOVED` in `tests/test_task_names_in_docs.py`. Write the pattern so that it does not match
  the replacement name, as the existing patterns do with a look-behind.
- The versioned 2.x documentation describes an earlier release and is not changed.

### Settings of the development stack

- A development task reads a stack setting (project name, published port, bind address,
  token) from `docker compose config --format json`, through `dev_settings()` in
  `tasks/dev.py`. A task that runs the stack under another project name or with other
  profiles, as the benchmark runner does for its destination, reads the same output through
  `resolved_compose()`. Do not copy a default into a Python constant, and do not parse the
  Compose YAML: both miss interpolation and shell overrides.
- A new setting is a `${NAME:-default}` interpolation in `development/docker-compose.yml`.
- [ADR 0029](https://github.com/opsmill/infrahub-sync/blob/feature/v3-develop/dev/adr/0029-the-development-stack-is-one-compose-file-with-profiles.md)
  records why.
