# OpsMill JPD Workflow Extension

Enforces an OpsMill Jira/JPD ticket reference (`infp-NNN` or `ifc-NNN`) when a
feature branch is created at the start of the SpecKit pipeline.

Registered as a mandatory `before_specify` hook: `/speckit-specify` (and the
`speckit-opsmill-prep` / `speckit-opsmill-auto` orchestrators that invoke it)
will not proceed until a valid ticket reference is provided, and the feature
branch is created as `<short-name>-<ticket-id>` (e.g.
`embeddable-python-library-infp-646`).

Adapted from the `infrahub` extension in the core infrahub repository; branch
creation itself is delegated to the `git` extension's `create-new-feature.sh`.
