# Contract: `invoke release.*` pin tasks (new `tasks/release.py`)

## `release.update-docker-compose --version X`

- Rewrites the `${VERSION:-…}` default on every Sync service image line of the root
  `docker-compose.yml` to `X`. A line counts as a Sync image line if it is an
  `image:` line that references `opsmill/infrahub-sync`. Every such line must be
  exactly `image: "${INFRAHUB_SYNC_DOCKER_IMAGE:-registry.opsmill.io/opsmill/infrahub-sync}:${VERSION:-…}"`;
  any other form (a hard-coded tag, `:latest`, an unclosed `${VERSION:-`) fails both
  tasks, which list every such line.
- Leaves every other byte unchanged, including comments, ordering and the
  third-party image pins.
- Exits non-zero if `X` is not a canonical PEP 440 version, or if no Sync image line
  is found. The two release workflows apply the same canonical-form rule.
- Is idempotent: a second run with the same `X` produces no diff.
- Runs for pre-releases too. Unlike infrahub, which bumps only for stable releases,
  every tag here must name an existing image.

## `release.validate-docker-compose --version X`

- Exits 0 only if every Sync image line pins exactly `X`.
- Otherwise exits non-zero and lists the offending lines and the version found on
  each.

## Callers

- `trigger-push-stable.yml` runs `update-docker-compose --version "${VERSION}"`
  after `uv lock`, and adds `docker-compose.yml` to the release PR's `git add` list.
- `release-publish.yml` runs `validate-docker-compose --version "${VERSION}"` before
  `gh release create`.

Unit tests (parametrized) cover:
- the rewrite;
- idempotence;
- a pre-release version;
- a missing image line;
- a mismatched pin;
- an untouched third-party image;
- a Sync image line outside the pinned form;
- the repository's own `docker-compose.yml` pinning `[project].version`.
