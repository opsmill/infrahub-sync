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

The candidate workflow (`.github/workflows/workflow-candidate.yml`) reclaims
`.image/archives/` after uploading it, in the same `candidate` job that builds
and qualifies the candidate. Its `packet` job restores the exact
`image-linux-amd64.tar` from the `infrahub-sync-candidate-image` artifact that
job already uploaded, alongside the recorded identity, bundle and
qualification-record artifacts, and runs `release.packet` against them. Doing
the same by hand, from a downloaded candidate, restores those same artifacts to
their original paths under `.image/` and `.release/` and then runs:

```sh
uv run invoke release.packet
```

The task writes the archive and checksum under `.release/packet/`. The
workflow's `packet` job publishes that directory as one artifact,
`infrahub-sync-candidate-packet`, kept for the same 30 days as the rest of a
candidate's uploads. The archive contains this directory:

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

### The rehearsal

The candidate workflow's `packet-rehearsal` job proves the packet works before
anyone hands it to a tester. It needs only the `packet` job and downloads
nothing else: one artifact, `infrahub-sync-candidate-packet`, on a runner that
has never checked this repository out and has never pulled or loaded the
image. That is the same starting point a tester's own host has.

The rehearsal runs every command the packet's own README gives a tester, and
adds verification that a tester's own read of the README would not catch on
its own:

1. Verify the outer archive against its `.sha256`, extract it, then verify
   `SHA256SUMS` inside `linux-amd64/` — the README's own commands.
2. `docker load` the image archive — the README's own command — then confirm
   the loaded image's `org.opencontainers.image.version` and
   `org.opencontainers.image.revision` labels match the `Version:` and
   `Commit:` the README states, which the README does not itself check.
3. Extract the Compose bundle, then `init`, `start`, and `status` it — the
   README's own commands; `status` has to report `READY`.
4. Confirm `./infrahub-sync-compose cli configs list` answers with nothing: a
   tester's first deployment has an empty configuration registry, which the
   README does not itself check.
5. Tear the deployment down with `./infrahub-sync-compose reset`, cleanup the
   README does not ask a tester to do but the rehearsal runner needs anyway.

A failing rehearsal means the packet a tester would receive does not work, not
that some other candidate artifact is wrong. Nothing here reads any input but
the packet itself.

Dispatching the workflow is the only way to run this end to end, and nobody
has done that yet. What a pull request can show is that the workflow
validates and that `tests/test_workflow_contracts.py` passes.
