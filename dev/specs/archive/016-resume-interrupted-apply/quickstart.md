# Quickstart: Resume an interrupted apply

Uses the development stack (`development/docker-compose.yml`) with the NetBox `demo` dataset.

1. Register and plan a configuration, then apply it:

   ```bash
   uv run infrahub-sync plan CONFIG@VERSION
   uv run infrahub-sync apply RUN_ID --expected-checksum CHECKSUM
   ```

2. While the apply runs, stop the worker container:

   ```bash
   docker compose -f development/docker-compose.yml kill sync-worker
   docker compose -f development/docker-compose.yml start sync-worker
   ```

3. Wait for the liveness monitor to end the execution, then view the run:

   ```bash
   uv run infrahub-sync runs show RUN_ID
   ```

   Expect `resumable: yes (verify required)`, the progress line and the per-kind counts.

4. Check the destination:

   ```bash
   uv run infrahub-sync verify RUN_ID
   ```

   Expect every `check: NAME passed` line and one `operation …: matches|differs|missing` line per operation after
   the marker.

5. Resume:

   ```bash
   uv run infrahub-sync runs resume RUN_ID --expected-checksum CHECKSUM
   ```

6. Confirm the result: `runs show RUN_ID` shows the run applied and `reconciliation_required: false`; a new
   `plan CONFIG@VERSION` shows 0 creates and 0 updates (SC-001).
