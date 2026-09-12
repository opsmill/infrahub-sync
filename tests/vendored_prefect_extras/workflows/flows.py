"""Real Prefect flow fixtures, resolvable offline.

Validation and ``load()`` must be exercised against genuine ``prefect.Flow``
objects rather than stand-ins, so this module holds the real thing. Importing it
and applying the ``@flow`` decorator needs no Prefect server, no network, and no
wall-clock sleeps; nothing here is ever executed by the catalogue, which only
stores the module and function names as strings.

Fixture targets and the identity each one exposes:

===========================  ======================  ==========================
Attribute                    ``Flow.name``           Purpose
===========================  ======================  ==========================
``my_sync_flow``             ``my-sync-flow``        Sync flow taking the
                                                     decorator's default name,
                                                     the dashed function name.
                                                     The matching ``flow_name``
                                                     for a valid definition.
``declared_name_flow``       ``declared-name``       Declared name differs from
                                                     the dashed function name
                                                     (``declared-name-flow``),
                                                     so a definition naming
                                                     either the function or the
                                                     declared name can produce
                                                     or avoid a flow-name
                                                     mismatch.
``my_async_flow``            ``my-async-flow``       Async flow -- ``isinstance``
                                                     identifies it as a
                                                     ``Flow`` just like the sync
                                                     one.
``plain_function``           n/a -- not a ``Flow``   Undecorated function, for
                                                     the not-a-flow message
                                                     and ``load()``'s
                                                     ``TypeError``.
===========================  ======================  ==========================

There is deliberately no missing attribute to reference: tests exercising
the missing-attribute path name an attribute this module does not define.
"""

from prefect import flow


@flow
def my_sync_flow() -> str:
    """Sync flow whose ``Flow.name`` defaults to the dashed function name."""
    return "my_sync_flow"


@flow(name="declared-name")
def declared_name_flow() -> str:
    """Flow whose declared name differs from its dashed function name."""
    return "declared_name_flow"


@flow
async def my_async_flow() -> str:
    """Async flow -- covered by the same ``isinstance(_, Flow)`` check."""
    return "my_async_flow"


def plain_function() -> str:
    """Undecorated function: a resolvable attribute that is not a ``Flow``."""
    return "plain_function"
