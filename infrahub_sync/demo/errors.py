"""Errors that stop the NetBox demo preparation."""

from __future__ import annotations


class DemoError(RuntimeError):
    """Base class: the demo preparation stopped, and the message names the cause."""


class DemoAssetError(DemoError):
    """A bundled demo asset (the schema or the NetBox package) cannot be found or read."""


class DemoPackageError(DemoError):
    """The NetBox package does not have the shape the address rewrite expects."""


class DemoSchemaError(DemoError):
    """Infrahub refused the demo schema or could not be reached to load it."""


class DemoAddressError(DemoError):
    """An address option carries `user:password@`; the message names the option, never the value."""
