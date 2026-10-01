# Publish the Sync image to Harbor and deploy from one `docker-compose.yml`

Operators got the Sync image as a per-architecture release attachment, bundled with
an `infrahub-sync-compose` wrapper and an image binding file. To qualify a build,
maintainers ran a candidate workflow, a tester packet and clean-host tests. That
was about 3200 lines of workflow and Invoke code. An operator had to verify
checksums, `docker load` a tarball and learn the wrapper's commands before
running anything.

This PR publishes one multi-arch image to `registry.opsmill.io/opsmill/infrahub-sync`
with the reusable `ci-docker-image.yml`. It builds linux/amd64 and linux/arm64 on
native runners and smoke-tests each one. Only when it publishes does it push, sign
with cosign and attach SPDX and CycloneDX SBOMs. Releases publish through
`trigger-release.yml` and `workflow-publish.yml`. A pre-release never moves
`latest`. The deployment is now the root `docker-compose.yml`, pinned to the
release version, which `trigger-push-stable.yml` bumps and `release-publish.yml`
validates. The image machinery shrank from 3195 to 490 lines (−84.7%).

## Before and after

```bash
# Before
sha256sum -c <release-archive>.tar.gz.sha256 && tar -xzf <release-archive>.tar.gz
docker load --input image-linux-amd64.tar
tar -xzf infrahub-sync-compose-<version>.tar.gz && cd infrahub-sync-compose-<version>
./infrahub-sync-compose init && ./infrahub-sync-compose start

# After
docker login registry.opsmill.io   # until 3.0.0
curl -fsSLO https://raw.githubusercontent.com/opsmill/infrahub-sync/<version>/docker-compose.yml
# write .env (docs/docs/compose-deployment.mdx, "An example .env")
docker compose up -d --wait
docker compose run --rm --no-deps -T cli configs list
```

## User-visible changes

- **Registry.** The image is `registry.opsmill.io/opsmill/infrahub-sync:<version>`,
  for linux/amd64 and linux/arm64, signed, with SBOMs attached. Releases no longer
  attach image tarballs. Until 3.0.0 the Harbor project is private, so a pull
  needs `docker login registry.opsmill.io`.
- **`docker-compose.yml`.** The operator deployment is the root
  `docker-compose.yml` of each release. `VERSION` and `INFRAHUB_SYNC_DOCKER_IMAGE`
  in `.env` select another tag or a mirror. The development stack moved to
  `development/docker-compose.dev.yml`.
- **Wrapper removed.** `infrahub-sync-compose`, `deploy/compose/`, the release
  bundle, the image binding file, `operator.env` and `.instance` are gone. The
  "Wrapper equivalents" table in `compose-deployment.mdx` maps each old command
  to its `docker compose` form.
- **CLI-less `.env` flow.** You write `.env` by hand, or generate it with the
  shell snippet in the docs, which needs no Python and no Sync CLI. The `cli`
  service runs the CLI through `docker compose run --rm --no-deps -T cli`.
- **Compose 2.24 minimum.** The file uses `configs.content`, so it needs Docker
  Compose 2.24 or later.
- **Maintainers.** The candidate workflow, the tester packet, `tasks/image.py`,
  `tasks/compose.py` and the clean-host tests are removed. Branch protection is
  unchanged: the PR gate still reports **Full qualification**. To publish by hand,
  see `docs/docs/develop/guides/publishing-an-image.md`.

## Rollout actions for maintainers

These are outside the code change:

1. **Before the first publish:** create the private Harbor project
   `opsmill/infrahub-sync` on `registry.opsmill.io`, and issue tester pull
   credentials (FR-015).
2. **Before the first publish:** confirm that the org variable `HARBOR_HOST` and
   the org secrets `HARBOR_USERNAME` and `HARBOR_PASSWORD` are shared with this
   repository.
3. Branch protection needs no change: the job id and name of "Full qualification"
   are preserved.
4. **At stable 3.0.0:** make the Harbor project public, and remove the "log in
   until 3.0.0" step from the docs.

After items 1 and 2, validate with a `workflow-publish.yml` dispatch
(`prerelease: true`, a throwaway version). It checks the push, the signature, the
SBOMs, and that `latest` does not move (quickstart §4 and §5).

## Approval checklist

- [x] Format and lint clean on changed areas. `invoke format` and `invoke lint`
  stop on rumdl, which flags the untracked, unrelated
  `.bug-analysis-release-body-from-notes.md`. rumdl on tracked files, ruff, pylint
  (10.0, 0 diagnostics), yamllint on tracked files and ty all pass.
- [x] The type-check command for the active Python profile exits 0. `ty check .`
  exits 0 with 16 `unused-ignore-comment` warnings, all in files this PR does not
  touch.
- [x] CLI behaviours validated (`--help`, `configs --help`, `runs --help`).
- [x] Docs updated if flags or config changed.
- [x] News fragment added: `changelog/+harbor-image-publishing.changed.md`.
- [x] Error handling uses specific exception types and clear messages.

Local evidence: `pytest -m "not docker and not compose"` gave 4916 passed and 117
skipped. The image smoke test against a labelled local build gave 28 passed. The
`-m compose` suite gave 37 passed in an earlier chunk.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
