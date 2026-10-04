"""Sync's own records in Infrahub: configurations, versions, approvals, and run mirrors.

Everything here reads or writes Infrahub data that belongs to Sync itself, through
the `Sync` schema extension, never the data a sync moves. It is used by the Sync
API and the service worker, and has no Prefect dependency.
"""
