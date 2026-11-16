"""Plan one source record whose peer list comes in a given order (spec 019 T013, FR-023).

Not a test module: it holds the driver that `test_netbox_peer_order.py`,
`test_nautobot_peer_order.py` and `test_genericrestapi_peer_order.py` share. No assertion lives
here.

`plan_peer_order` builds a source adapter of the class under test without its provider client,
as `tests/adapters/test_reference_conversion.py` does, stores three `BuiltinTag` peers, converts
one `OrganizationRIR` record through the adapter's own `record_to_data`, and derives the
operations of that source against a destination through `operations_from_diff`, the function a
plan uses. A test calls it with a peer list and with the same list reversed, and compares the
two results.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from diffsync import Adapter
from diffsync.store.local import LocalStore

from infrahub_sync import SchemaMappingField, SchemaMappingModel, SyncAdapter, SyncConfig
from infrahub_sync.plan.derive import operations_from_diff

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from infrahub_sync.plan.models import PlannedOperation

TAG_KIND = "BuiltinTag"
OWNER_KIND = "OrganizationRIR"
OWNER_NAME = "RIPE"
# Unsorted on purpose: the reversed list differs from the list, and neither is sorted.
PEER_NAMES = ("mike", "zulu", "alpha")

LIST_FIELD = SchemaMappingField(name="tags", mapping="tags", reference=TAG_KIND)

CONFIG = SyncConfig(
    name="peer-order",
    source=SyncAdapter(name="source"),
    destination=SyncAdapter(name="infrahub"),
    order=[TAG_KIND, OWNER_KIND],
    schema_mapping=[
        SchemaMappingModel(name=TAG_KIND, mapping="tags", fields=[SchemaMappingField(name="name", mapping="name")]),
        SchemaMappingModel(
            name=OWNER_KIND,
            mapping="rirs",
            fields=[SchemaMappingField(name="name", mapping="name"), LIST_FIELD],
        ),
    ],
)


def adapter_class(base: type[Adapter], tag_model: type[Any], owner_model: type[Any]) -> type[Adapter]:
    """A subclass of the adapter under test that declares the two kinds, as a generated one does."""
    return type(
        f"{base.__name__}PeerOrder",
        (base,),
        {"top_level": [TAG_KIND, OWNER_KIND], TAG_KIND: tag_model, OWNER_KIND: owner_model},
    )


def _bare(cls: type[Adapter], target: str) -> Any:  # noqa: ANN401 - one of three adapter classes
    """An instance of `cls` built without its provider client, with an empty local store."""
    instance = cls.__new__(cls)
    Adapter.__init__(instance)  # noqa: PLC2801 - the adapter is built without its provider client
    instance.store = LocalStore()
    instance.target = target
    instance.config = SimpleNamespace(schema_mapping=[])
    return instance


def plan_peer_order(
    cls: type[Adapter],
    *,
    peer_ids: Sequence[Any],
    record_id: Any,  # noqa: ANN401 - each provider has its own ID type
    source_object: Callable[[dict[str, Any]], dict[str, Any]],
    destination_tags: Sequence[str] | None,
) -> list[PlannedOperation]:
    """The operations of one owner record whose `tags` lists `peer_ids` in the given order.

    The destination holds the three peers, and the owner too when `destination_tags` is given,
    with those tags. `source_object` builds the object shape the adapter's loader hands to its
    converter from the decoded payload.
    """
    names: Mapping[str, str] = dict(
        zip((str(peer_id) for peer_id in sorted(peer_ids, key=str)), PEER_NAMES, strict=True)
    )
    source = _bare(cls, "source")
    destination = _bare(cls, "destination")
    tag_model = getattr(cls, TAG_KIND)
    owner_model = getattr(cls, OWNER_KIND)
    for local_id, name in names.items():
        source.add(tag_model(name=name, local_id=local_id))
        destination.add(tag_model(name=name, local_id=local_id))

    payload = {"id": record_id, "name": OWNER_NAME, "tags": [{"id": peer_id} for peer_id in peer_ids]}
    element = SchemaMappingModel(name=OWNER_KIND, fields=[SchemaMappingField(name="name", mapping="name"), LIST_FIELD])
    data = source.record_to_data(source_object(payload), element, owner_model)
    source.add(owner_model(**data))
    if destination_tags is not None:
        destination.add(owner_model(name=OWNER_NAME, tags=list(destination_tags), local_id="destination-owner"))

    return operations_from_diff(
        destination.diff_from(source),
        config=CONFIG,
        tier_of=lambda _kind: 0,
        source_adapter=source,
        destination_adapter=destination,
    )


__all__ = ["OWNER_KIND", "PEER_NAMES", "TAG_KIND", "adapter_class", "plan_peer_order"]
