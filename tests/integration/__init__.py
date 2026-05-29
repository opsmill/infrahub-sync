# Integration tests for infrahub-sync.
#
# These tests require a running Infrahub instance and are skipped when
# `INFRAHUB_ADDRESS` + `INFRAHUB_API_TOKEN` aren't set in the environment.
# Run locally with:
#
#     INFRAHUB_ADDRESS=http://localhost:8000 \
#     INFRAHUB_API_TOKEN=<token> \
#     pytest tests/integration -m integration
