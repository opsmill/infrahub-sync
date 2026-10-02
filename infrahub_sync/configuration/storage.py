"""The V3 policy for temporary synchronization data."""

UNSUPPORTED_STORE_REASON = "unsupported-sync-store"
UNSUPPORTED_STORE_MESSAGE = (
    "Configured sync stores, including Redis, are not supported in V3. "
    "Remove the store block to keep sync data in memory."
)


class UnsupportedSyncStoreError(ValueError):
    """Refuse a configured store with a fixed, safe diagnostic."""

    def __init__(self) -> None:
        """Use the shared refusal without including configuration values."""
        super().__init__(UNSUPPORTED_STORE_MESSAGE)
