# Changelog

This project uses [*towncrier*](https://towncrier.readthedocs.io/) and the changes for the upcoming release can be found in <https://github.com/opsmill/infrahub-sync/tree/main/changelog>.

<!-- towncrier release notes start -->

## [Infrahub Sync - v2.1.0](https://github.com/opsmill/infrahub-sync/tree/2.1.0) - 2026-11-18

### Added

- Added a Webex source adapter with configurable read-only resources, token renewal, pagination and an example mapping locations, workspaces and devices with peer relationships. Default retry delays are capped by the `max_retry_delay` setting, a `Retry-After` longer than `max_retry_delay` stops the run, and `{env: ...}` references are accepted in `org_id` and `params` only; the `*_env` credential settings take environment variable names.
- Added the `msgraphsync` source adapter, which reads users, groups and group membership from Microsoft Entra ID through Microsoft Graph and loads them into Infrahub. The adapter needs the optional packages `msgraph-sdk` and `azure-identity`: `pip install msgraph-sdk azure-identity`.

### Changed

- Sync now removes peers added outside sync from a mapped relationship that holds several peers, except when a desired peer cannot be found in the store. ([#168](https://github.com/opsmill/infrahub-sync/issues/168))

### Fixed

- During an Infrahub update, a relationship that holds several peers is now fetched before its peers are compared, so stale peers are removed. ([#168](https://github.com/opsmill/infrahub-sync/issues/168))
- Running `diff` no longer forces a full extract on the next `sync --no-full-extract`. Only applied runs are used as the incremental baseline, and the reason for each per-resource full extract is logged. ([#403](https://github.com/opsmill/infrahub-sync/issues/403))
- Loading the Infrahub side of a sync now resolves relationships to nodes of the same kind before they are loaded, without including nodes excluded by filters.
- Updating an Infrahub node no longer fetches the schema from Infrahub, and the release pull request exemption from the bump-label check now requires the title to name the same version as its `release/<version>` branch.

### Housekeeping

- Releases are now prepared as a reviewable pull request instead of being committed straight to `main`. Dispatching `trigger-push-stable.yml` bumps the version, assembles `CHANGELOG.md` from the news fragments under `changelog/` and opens a `chore(release)` pull request; merging it creates the tag and publishes a GitHub Release whose body is the changelog section contributors wrote. Release Drafter, which generated release notes from pull request titles, has been removed.
- Standardized release bump labels and now reject pull requests with missing or conflicting release intent.
- The changelog check now counts only newly added news fragments, so editing an existing fragment no longer satisfies it. The check's `pull_request` trigger is disabled, so it does not run on pull requests yet.

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
