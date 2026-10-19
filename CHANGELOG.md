# Changelog

This project uses [*towncrier*](https://towncrier.readthedocs.io/) and the changes for the upcoming release can be found in <https://github.com/opsmill/infrahub-sync/tree/main/changelog>.

<!-- towncrier release notes start -->

## [Infrahub Sync - v2.0.1](https://github.com/opsmill/infrahub-sync/tree/2.0.1) - 2026-09-11

### Changed

- infrahub-sync no longer installs the `infrahub-sdk[all]` extra. It declares `jinja2`, `pyyaml`
  and `typer` directly, which are the only packages it used from that extra, so a plain install
  pulls fewer unrelated packages. infrahub-sdk 1.23.2 is the tested version; the supported range
  stays `>=1.17,<2` so infrahub-sync can be paired with the SDK release an Infrahub server needs.

### Fixed

- A plain `pip install infrahub-sync` now includes `requests` and `urllib3`, which product code
  imports but which previously arrived only through the `dev` extra. `packaging`, `pydantic` and
  `typing-extensions` are declared directly instead of relying on other packages to bring them. The
  `netutils` and `pyarrow` lower bounds now match the oldest versions that work on Python 3.13.
  The locked and tested diffsync is now 2.2.3; the previously locked 2.2.2 was yanked upstream.
- Apply source and owner lineage to relationship edges added or changed during an
  Infrahub destination update.
- Fixed NetBox interface synchronization failing for tagged and tagged-all L2 modes.
  NetBox q-in-q mode is explicitly refused because the example destination schema cannot
  represent it; malformed non-null modes also fail contextually at the transform boundary.
- Fixed generated DiffSync model annotations for schemas read from the Infrahub API.
  Optional attributes and many relationships are annotated and defaulted correctly again,
  and string attribute defaults are now emitted as Python literals, so defaults containing
  quotes, newlines or backslashes stay valid Python and keep their exact schema value.

### Housekeeping

- Development tools declared without a lower bound now have one, so a lowest-version dependency
  resolution can no longer select releases that predate Python 3. The `typer-cli` floor matters
  most: older releases pull `typer-slim`, which shadows the real `typer` package and breaks the CLI.
