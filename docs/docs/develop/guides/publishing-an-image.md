---
title: "Publishing an image by hand"
---

## Publishing an image by hand

A release publishes its own image. Any other image, such as a pre-release build
for testers or an image of a fix that has not shipped yet, comes from a manual
dispatch of the image workflow, `.github/workflows/ci-docker-image.yml`. The
dispatch builds one commit for the platforms you choose, smoke-tests every
platform, and, when publishing, pushes the image to the Harbor
project under the tags you list. It then
signs the image and attaches its bill of materials.

### When to dispatch

Dispatch the workflow when someone needs an image that no release provides:

- a pre-release image for testers, under a tag such as `3.0.0a6-rc1`;
- an image of a branch head, so a reviewer can run a change before it merges;
- a build-only run, with `publish` off, to check that a commit still builds and
  passes the smoke test on both platforms.

Cut a release instead when the image should carry a version and, for a stable
release, the `latest` tag.

### The inputs

The dispatch takes the same six inputs a release passes when it calls the
workflow.

| Input | Value |
|---|---|
| `ref` | The commit to build. Use a full 40-character SHA. |
| `tags` | Newline-separated full image references on the `HARBOR_HOST` name, such as `<HARBOR_HOST>/opsmill/infrahub-sync:dispatch-test`. |
| `labels` | Newline-separated OCI labels: `org.opencontainers.image.source`, `.version` and `.revision`. |
| `publish` | `true` to push, sign and attest. `false` builds and smoke-tests only. |
| `platforms` | Comma-separated, `linux/amd64,linux/arm64` by default. Name one platform to build only that one. |
| `version` | Optional. Names the bill-of-materials artifact the run uploads. |

The smoke test refuses an image whose `org.opencontainers.image.revision`
label names a commit other than the one checked out from `ref`. Set the
revision label to the same full SHA as `ref`.

`tags` and `labels` hold more than one line, and the **Run workflow** form in
the Actions tab takes one line per input. Dispatch with `gh workflow run` and a
JSON body instead, so the newlines survive.

Tag the image with the repository variable `HARBOR_HOST`, not
`registry.opsmill.io`. The workflow logs in to the `HARBOR_HOST` name only, so
a push addressed to `registry.opsmill.io` fails with `401 Unauthorized`. The
pushed image is then pulled through `registry.opsmill.io`, which serves the same
Harbor project.

### A worked example

This publishes the head of `main` as `dispatch-test`. Pull it afterwards as
`registry.opsmill.io/opsmill/infrahub-sync:dispatch-test`:

```sh
SHA=$(git rev-parse origin/main)
HARBOR_HOST=$(gh variable get HARBOR_HOST --repo opsmill/infrahub-sync)
jq -n --arg sha "$SHA" --arg host "$HARBOR_HOST" '{
  ref: $sha,
  tags: "\($host)/opsmill/infrahub-sync:dispatch-test",
  labels: ([
    "org.opencontainers.image.source=https://github.com/opsmill/infrahub-sync",
    "org.opencontainers.image.version=dispatch-test",
    "org.opencontainers.image.revision=\($sha)"
  ] | join("\n")),
  publish: "true",
  version: "dispatch-test"
}' | gh workflow run ci-docker-image.yml --repo opsmill/infrahub-sync --ref main --json
```

The `--ref main` flag picks the branch the workflow definition is read from.
The `ref` input picks the commit that gets built. The two can differ.

A run is named after its inputs, for example
`Image 0123abcd… (publish=true)`, so you can find it in the run list. A
newer build-only run for the same `ref` cancels a build-only run still in
progress. Publishing runs use a separate concurrency group and are never
cancelled, so one can't stop between pushing a tag and signing it; a second
publishing run for the same `ref` waits for the first.

A dispatch with `publish` set to `true` fails before it builds anything when
`tags` is empty, or when the repository has no `HARBOR_HOST` variable.

### Verify the result

When the run finishes, check the image from any host:

```sh
docker login registry.opsmill.io
docker pull registry.opsmill.io/opsmill/infrahub-sync:dispatch-test
docker buildx imagetools inspect registry.opsmill.io/opsmill/infrahub-sync:dispatch-test
cosign verify registry.opsmill.io/opsmill/infrahub-sync:dispatch-test \
  --certificate-identity-regexp 'https://github.com/opsmill/infrahub-sync/' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
cosign verify-attestation --type spdxjson \
  --certificate-identity-regexp 'https://github.com/opsmill/infrahub-sync/' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  registry.opsmill.io/opsmill/infrahub-sync:dispatch-test
cosign verify-attestation --type cyclonedx \
  --certificate-identity-regexp 'https://github.com/opsmill/infrahub-sync/' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  registry.opsmill.io/opsmill/infrahub-sync:dispatch-test
```

The `docker login` step is needed until 3.0.0.
Expect these results:

- the manifest list holds `linux/amd64` and `linux/arm64`, or only the
  platform you named;
- the signature and both attestations verify;
- `latest` still points where it did before the run.

### Why a dispatch leaves `latest` alone

The workflow pushes exactly the references listed in `tags` and nothing else.
It never adds `latest` or a version tag of its own. Only the release path
decides `latest`: it adds that tag for a stable release that GitHub also
reports as its latest release, and never for a pre-release. A dispatch moves
`latest` only if you list
`<HARBOR_HOST>/opsmill/infrahub-sync:latest` in `tags` yourself. Don't,
unless you mean to replace the image every `latest` user pulls next.
