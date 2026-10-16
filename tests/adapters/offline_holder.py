"""An adapter instance with no provider client, for running `record_to_data` offline.

`record_to_data` resolves references through the base class's `peer_unique_id`, which keeps
its state on the adapter instance. These tests build the instance without `__init__`, so no
client is created, and give it only a store and an empty mapping configuration.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, TypeVar

T = TypeVar("T")


def offline_adapter(adapter_cls: type[T], store: Any, *, target: str = "source") -> T:  # noqa: ANN401 - any store stub
    """Return an instance of `adapter_cls` holding `store`, created without its client."""
    holder = object.__new__(adapter_cls)
    holder.store = store
    holder.target = target
    holder.config = SimpleNamespace(schema_mapping=[])
    return holder
