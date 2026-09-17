Rewrote Install and Create a sync project so a new reader lands on the V3 path: Docker
Compose is the supported deployment and the CLI is a client of the Sync API, not a local
runner. Added a Configuration package page covering the package envelope, what registration
returns, how versions and validation work, idempotency keys, and how a package references
credentials. `docs/sidebars.ts` and the readme's "Define a sync project" and "Get started"
sections now link the three pages in sequence.
