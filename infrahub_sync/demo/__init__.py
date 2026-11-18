"""Prepare a Sync deployment and its Infrahub for the NetBox demo.

The preparation loads the demo's destination schema into Infrahub and registers the
`from-netbox` configuration package, with the NetBox and Infrahub addresses the worker uses,
through the Sync API. Running it again with the same addresses changes nothing. It never
plans, applies, approves or changes whether the Sync API requires approval: the first plan
stays a step the person trying the demo takes.

This `__init__` imports none of the submodules, so importing `infrahub_sync.demo.package`
loads neither `prepare` nor the Infrahub SDK. Python still runs `infrahub_sync/__init__.py`
first, which loads the Sync client.
"""
