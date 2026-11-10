# Quickstart: validate approval and the destination-checking `verify`

## Unit tier (offline)

```bash
uv sync --extra dev --extra prefect --extra service
uv run invoke tests.tests-unit
uv run ruff check .
uv run invoke lint
```

The new and changed tests are listed in [tasks.md](tasks.md). They cover each check of the approval contract, each
refusal of the approval-required setting, and, for every pre-write check, that `verify` and `apply` fail on the same
check (SC-004).

## Live check against the development stack

Prerequisites: the development stack (`development/docker-compose.yml`), a registered configuration package, and a
Sync API token in `SYNC_API_TOKEN`.

1. Plan a run and read its checksum:

   ```bash
   infrahub-sync plan --config CONFIG --version VERSION --reason "approval check"
   infrahub-sync runs plan RUN_ID
   ```

2. Approve it without applying, and confirm the destination did not change:

   ```bash
   infrahub-sync approve RUN_ID --expected-checksum CHECKSUM
   infrahub-sync runs show RUN_ID     # approved_by, approved_at, approved_checksum
   ```

   Approving again is refused with `already-approved`.

3. Verify against the destination:

   ```bash
   infrahub-sync verify RUN_ID       # one "check: … passed" line per check
   ```

   Remove a planned attribute from the destination schema (with `state: absent`), run `verify` again, and confirm it
   fails on `destination_schema` or `planned_fields` and writes nothing.

4. Require approval: restart the Sync API with `INFRAHUB_SYNC_REQUIRE_APPROVAL=true`, then:

   ```bash
   infrahub-sync whoami              # approval_required: true
   infrahub-sync sync --config CONFIG --version VERSION   # refused: approval-required-for-sync
   infrahub-sync apply OTHER_RUN_ID --expected-checksum CHECKSUM   # unapproved: approval-required
   infrahub-sync apply RUN_ID --expected-checksum CHECKSUM         # approved: applies
   ```

5. Turn the setting off and confirm `apply` and `sync` behave as before.
