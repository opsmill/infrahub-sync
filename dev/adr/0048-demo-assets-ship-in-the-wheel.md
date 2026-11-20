# 48. The NetBox demo's schema and package ship in the wheel

**Status**: Accepted
**Date**: 2026-11-18
**Source**: `pyproject.toml`, `Dockerfile`, `.dockerignore`, `infrahub_sync/demo/assets.py`,
`tests/demo/test_wheel_assets.py`, `tests/image/test_demo_assets.py`

## Context

`infrahub-sync demo prepare` loads a destination schema into Infrahub and registers the
`from-netbox` configuration package. It must run from the published image, for example in a
Kubernetes deployment, where no checkout of this repository exists.

The two inputs already live in the repository, each at a path other checks read:

- the schema, `tests/data/nightly_schema/traditional-infrastructure-sot/` (16 files), whose bytes
  the nightly runner checks against a recorded SHA-256 digest and which the status translation
  test reads;
- the package, `examples/netbox_to_infrahub/package.yml`, which the example checks read.

The image installs the project as a built wheel (`uv sync --no-editable`), so it carries only what
the wheel carries.

## Decision

- The wheel carries both inputs under `infrahub_sync/demo/assets/`, through hatchling
  `force-include` in `pyproject.toml`: the schema directory as `schema/` and the package as
  `netbox/package.yml`. The repository keeps one copy of each file, at its current path.
- `infrahub_sync/demo/assets.py` reads the bundled directory through `importlib.resources`. An
  editable install has no such directory, because `force-include` applies only to a built wheel,
  so the module then reads the two repository paths.
- The `Dockerfile` copies the two source paths into the build stage, at their repository paths,
  before the wheel build. `.dockerignore` admits only those two paths from the `examples` and
  `tests` trees, and `tests/image/test_build_context.py` checks that nothing else from either
  tree reaches the build context.
- The `image_inputs` filter in `.github/file-filters.yml` names the two paths, so a change to
  either builds and tests the image.

## Consequences

- The package published to PyPI carries the demo schema and package, about 17 files of YAML. A
  user who installs the CLI from PyPI gets a working `demo prepare` command.
- There are two ways to find the inputs, the bundled path and the repository path. Each has its
  own test: `tests/demo/test_assets.py` for the repository path, `tests/demo/test_wheel_assets.py`
  for the built wheel, and `tests/image/test_demo_assets.py` for the image.
- A change to the schema or the package reaches the wheel and the image without a second edit.
  Moving either file needs a change to `pyproject.toml`, the `Dockerfile`, `.dockerignore`,
  `.github/file-filters.yml` and `infrahub_sync/demo/assets.py` together; the wheel build fails
  when a `force-include` source is missing.
- In the development stack, the source tree is mounted over the installed package, which hides
  the bundled directory. `dev.demo` therefore runs `demo prepare` on the host, where the
  repository paths exist.

## Alternatives considered

- **Copy the files into `infrahub_sync/demo/assets/` in git.** Rejected: two copies of each file
  that can drift, and the schema's recorded digest would check only one of them.
- **Copy the files into the image only, for example under `/opt/infrahub-sync/demo`.** Rejected:
  the command would not work from a wheel, and the path would become part of the image's contract.
- **Fetch the schema from the schema library when the command runs.** Rejected: the command must not
  fetch either input from the network, and a request to the schema library can time out.
