"""The immutable, explicitly composed workflow catalogue.

Depends *downward* on ``definitions.py`` -- this feature's common internal
module -- and on nothing else beyond the standard library. It never imports a
sibling feature, and it never imports a workflow implementation module: a
catalogue handles a definition's ``module`` and ``function`` strings only by
storing them.

There is no global registry, no decorator, and no import-time side effect. An
application builds its catalogue by calling :class:`WorkflowCatalogue` at its
own composition root, passing definitions and "definition groups" -- which are
plain iterables, not a class of ours.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator

from opsmill_prefect_extras.workflows.definitions import (
    _KEY_FORMAT,
    WorkflowDefinition,
)

MAX_LISTED_KEYS: int = 10
"""How many known keys a lookup-miss message lists before summarizing."""


class DuplicateWorkflowError(ValueError):
    """Two composed definitions claim one deployment identity.

    A :exc:`ValueError` subclass, so a caller who does not import this name
    still catches the collision conventionally. The colliding
    ``flow_name/deployment_name`` is available as :attr:`key` and appears in
    the message.

    Attributes:
        key: The identity both definitions rendered, as
            ``flow_name/deployment_name``.
    """

    key: str

    def __init__(self, key: str) -> None:
        """Report the collision, naming the identity both definitions claim.

        Args:
            key: The colliding ``flow_name/deployment_name`` identity.
        """
        super().__init__(
            f"{key!r} is claimed by two composed definitions -- a deployment "
            f"identity ({_KEY_FORMAT}) is unique to one definition, whether "
            f"they were supplied individually, within one group, or across "
            f"groups; compose one of them out, or give it its own deployment "
            f"name"
        )
        self.key = key


class WorkflowCatalogue:
    """An immutable collection of workflow definitions, keyed by identity.

    Composed explicitly and read-only thereafter: there are no mutator
    methods, so building a different catalogue is the only way to get
    different contents.

    Deliberately **not** a :class:`collections.abc.Mapping`: iteration yields
    definitions rather than keys, because the definition is what every consumer
    goes on to use. Keys remain available through :meth:`keys` and
    ``key in catalogue``.

    Example:
        Composing this imports nothing from ``example_app`` -- not even the modules
        the definitions name::

            from opsmill_prefect_extras.workflows import (
                WorkflowCatalogue,
                WorkflowDefinition,
            )

            INVENTORY_REFRESH = WorkflowDefinition(
                flow_name="inventory-refresh",
                deployment_name="scheduled",
                module="example_app.inventory.flows",
                function="refresh_inventory",
                tags=("inventory",),
            )
            # A "definition group" is any iterable of definitions.
            REPORT_WORKFLOWS = (
                WorkflowDefinition(
                    flow_name="reports",
                    deployment_name="nightly",
                    module="example_app.reports.flows",
                    function="nightly_report",
                ),
            )

            CATALOGUE = WorkflowCatalogue(INVENTORY_REFRESH, REPORT_WORKFLOWS)

            CATALOGUE["inventory-refresh/scheduled"]          # lookup by key
            [definition.key for definition in CATALOGUE]       # composition order
            "inventory-refresh/scheduled" in CATALOGUE         # True
    """

    __slots__ = ("_definitions",)

    _definitions: dict[str, WorkflowDefinition]

    def __init__(
        self, *sources: WorkflowDefinition | Iterable[WorkflowDefinition]
    ) -> None:
        """Compose a catalogue from definitions and definition groups.

        Sources are flattened in argument order; a group contributes its
        definitions in its own iteration order, at its position among the
        arguments. Nothing is imported and nothing is resolved: ``module`` and
        ``function`` are only stored. Composing no sources at all yields a
        valid, empty catalogue.

        Args:
            *sources: Each either a single :class:`WorkflowDefinition` or any
                iterable of them (a "definition group" -- domain packages
                declare plain tuples). Iterables are consumed once, here, so
                generators are fine.

        Raises:
            DuplicateWorkflowError: If two of the flattened definitions share a
                ``(flow_name, deployment_name)`` identity, however they were
                supplied. Raised on the second occurrence, so no definition is
                ever silently overwritten.
        """
        definitions: dict[str, WorkflowDefinition] = {}
        seen_identities: set[tuple[str, str]] = set()
        for source in sources:
            group: Iterable[WorkflowDefinition]
            if isinstance(source, WorkflowDefinition):
                group = (source,)
            else:
                group = source
            for definition in group:
                # The identity tuple, not the rendered key, is the detection
                # basis; the two agree because `/` is banned in both
                # name halves, which is what keeps the key injective.
                identity = (definition.flow_name, definition.deployment_name)
                if identity in seen_identities:
                    raise DuplicateWorkflowError(definition.key)
                seen_identities.add(identity)
                definitions[definition.key] = definition

        self._definitions = definitions

    def __getitem__(self, key: str) -> WorkflowDefinition:
        """Look a definition up by its ``flow_name/deployment_name`` key.

        Args:
            key: The definition's derived key.

        Returns:
            The stored definition.

        Raises:
            KeyError: If no definition has that key -- never a silent
                default. The message names the missing key, states the key
                convention, and lists the known keys, so the commonest miss
                (a bare flow name) corrects itself. It is a single line on
                purpose: :exc:`KeyError` reprs its argument, which would
                otherwise escape the newlines.
        """
        try:
            return self._definitions[key]
        except KeyError:
            raise KeyError(self._lookup_miss_message(key)) from None

    def __iter__(self) -> Iterator[WorkflowDefinition]:
        """Iterate the definitions -- not the keys -- in composition order.

        Returns:
            An iterator over the definitions, ordered as they were composed.
        """
        return iter(self._definitions.values())

    def __len__(self) -> int:
        """Count the definitions.

        Returns:
            How many definitions this catalogue holds.
        """
        return len(self._definitions)

    def __contains__(self, item: object) -> bool:
        """Test membership of a key.

        Args:
            item: A ``flow_name/deployment_name`` key, or anything else.

        Returns:
            ``True`` if a definition has that key; ``False`` for a miss and
            for every non-``str`` object.
        """
        return isinstance(item, str) and item in self._definitions

    def keys(self) -> tuple[str, ...]:
        """List the keys in composition order.

        Returns:
            Every definition's key as a tuple, ordered like iteration.
        """
        return tuple(self._definitions)

    def _lookup_miss_message(self, key: str) -> str:
        """Build the actionable lookup-miss message.

        Args:
            key: The key that was asked for and not found.

        Returns:
            A single-line message naming the missing key, the key convention,
            the total number of known keys, and up to
            :data:`MAX_LISTED_KEYS` of them.
        """
        known = self.keys()
        listed = ", ".join(known[:MAX_LISTED_KEYS])
        if not known:
            listed = "<none -- this catalogue is empty>"
        elif len(known) > MAX_LISTED_KEYS:
            listed = f"{listed}, ... (+{len(known) - MAX_LISTED_KEYS} more)"
        return (
            f"{key!r} is not in this catalogue -- keys are rendered as "
            f"{_KEY_FORMAT}, so a bare flow name never matches; "
            f"{len(known)} known key(s): {listed}"
        )
