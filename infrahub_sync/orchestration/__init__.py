"""Optional Prefect orchestration for Infrahub Sync.

The ONLY package that imports `prefect`, and only in its leaf modules
(`flow`, `serve`) — never here, so importing this package cannot pull Prefect
into a base install. Requires the optional extra:
`pip install -e '.[prefect]'` from the repository checkout.
"""
