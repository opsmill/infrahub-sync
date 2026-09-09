"""The version this deployment serves, for the driver to hold to the record."""

from __future__ import annotations

from kit import deployment

with deployment() as client:
    print(client.get_version().server_version)
