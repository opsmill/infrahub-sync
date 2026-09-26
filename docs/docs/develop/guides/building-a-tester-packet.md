---
title: "Building a private tester packet"
---

## Building a private tester packet

A private candidate can be handed to a Linux amd64 tester as one archive and
its checksum file. The packet contains the qualified Infrahub Sync image, the
Compose deployment bundle, a declared example configuration, and instructions.
It does not publish an image or register the example configuration.

### Inputs and records

Build the candidate image and run `release.kit` before qualification. The kit
records the SHA-256 of the Linux amd64 export, the Compose bundle, and
`examples/tester_packet/example-package.yml` in
`.release/bundle/candidate-input.json`. The qualification step writes
`.release/qualification.json` after the required image and Compose checks pass.

The packet task reads both records. It compares the three input files with
their recorded checksums, checks that the qualified image configuration digest
matches the Docker-load archive, and checks that the bundle and release
identity match the qualification record. A mismatch stops the task before it
writes a packet.

The candidate workflow currently reclaims `.image/archives/` after uploading
it. Restore the exact `image-linux-amd64.tar` from the candidate image artifact
to `.image/archives/` before building a packet from those artifacts. Keep the
recorded bundle and qualification files under `.release/` at their original
paths. Then run:

```sh
uv run invoke release.packet
```

The task writes the archive and checksum under `.release/packet/`. The archive
contains this directory:

```text
private-candidate-<version>-<sha7>/linux-amd64/
  SHA256SUMS
  README.md
  example-package.yml
  image-linux-amd64.tar
  infrahub-sync-compose-<version>.tar.gz
```

The outer filenames are `private-candidate-<version>-<sha7>.tar.gz` and
`private-candidate-<version>-<sha7>.tar.gz.sha256`.

`SHA256SUMS` covers the image, bundle, and example package. The outer checksum
covers the whole archive, including its README. The README links to the four
operator guides at the candidate's full commit. The example package contains
URL placeholders and environment credential references. A tester replaces the
placeholders and loads a matching destination schema before registration.

The Infrahub Sync image is loaded from the packet. Docker still pulls the
PostgreSQL, Prefect, and object-store images when the Compose stack starts.
