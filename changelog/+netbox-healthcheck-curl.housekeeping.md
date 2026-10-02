`invoke netbox.up` and `invoke netbox.seed` now return as soon as the local NetBox is ready, because its health check uses `curl` instead of `wget`, which the pinned NetBox image does not include.
