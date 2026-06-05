"""Integration tests for infrahub-sync.

These tests require a running Infrahub instance and are skipped when
``INFRAHUB_ADDRESS`` and ``INFRAHUB_API_TOKEN`` are not set in the
environment. Run locally with::

    INFRAHUB_ADDRESS=http://localhost:8000 \\
    INFRAHUB_API_TOKEN=<token> \\
    pytest tests/integration -m integration
"""
