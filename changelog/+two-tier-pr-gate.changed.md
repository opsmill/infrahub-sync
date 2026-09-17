Pull requests to the V3 development branch now run a fast continuous-integration tier on every push
and the full qualification tier only when the change is believed ready. Lint, the uv check, the unit
tests and an amd64 image build with its bill of materials, vulnerability scan and smoke run on every
push; the arm64 platform, the warm-builder freshness check, the Compose lifecycle and the clean-host
matrix run when the pull request carries the `qualify` label or its diff touches a path only those
stages cover. No check was removed or weakened — every gate that guarded a merge still guards it —
and a new always-running `Full qualification` check refuses a head the full tier never covered, so a
pull request cannot merge without it.
