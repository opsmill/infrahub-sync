# Fixture validation and operator quickstart

The destination must already have the location hierarchy and optional Text LocationSite.webex_location_id. A separate workflow writes Webex location IDs onto matching existing sites. Fixtures use populated, unique destination site IDs and are not a schema migration.

```bash
uv sync --extra dev
uv run pytest -q tests/adapters/test_webex_client.py tests/adapters/test_webex.py tests/adapters/test_webex_examples.py
uv run infrahub-sync list --directory examples/
```

The tests generate the meeting-room project's models, load invented Webex responses and an in-memory destination, verify correct parents and zero site writes, and confirm that a second diff has no changes. No live services are contacted.

For engineer-controlled live validation, provide credentials through environment variables, prepare site IDs separately, generate the example models against the destination schema and review the diff before approving writes. Destinations containing sites without webex_location_id remain subject to the existing loading limitation; resolving it is a follow-up, not a flag in this adapter. See the adapter page for the exact generate/diff commands.
