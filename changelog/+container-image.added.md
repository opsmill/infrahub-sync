Added a container image that supplies the Sync API, the service worker, the deployment
bootstrap, and one-off CLI or Python commands from one artifact. It installs the
committed lock frozen, runs as a non-root user with a read-only root filesystem and
three writable paths, and records its version, source revision, and creation time as
standard OCI labels. `invoke image.build`, `image.inspect`, `image.smoke`, `image.sbom`,
`image.scan`, and `image.clean` build it, check it, and enforce the vulnerability policy
locally; see the [container image page](https://docs.infrahub.app/sync/container-image).
