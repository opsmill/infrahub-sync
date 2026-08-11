Added an authenticated managed Sync HTTP API for creating and inspecting durable runs,
reviewing retained plans, running read-only verification, applying an exact approved
checksum, retrieving results and artifacts, and requesting cancellation through Prefect.
Actor-scoped durable mutation receipts make exact retries converge on one Sync run and
Prefect flow run without storing the raw client key. Install the `managed` extra to run the
separate Prefect deployment and API; the base CLI and existing four-parameter Developer
Preview deployment remain unchanged.
