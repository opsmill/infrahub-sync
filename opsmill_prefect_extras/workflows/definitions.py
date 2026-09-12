"""Immutable workflow definitions -- this feature's **common internal module**.

This module is the ``workflows`` feature's designated **common internal
module**: the one place a shape shared across the feature lives, and this
docstring is the durable in-package record of that designation. ``catalogue.py``
and ``validation.py`` depend *downward* on it; it depends on nothing but the
standard library and ``prefect.flows.Flow``. Nothing here may ever import a
sibling module of this package, and any future feature that needs the
workflow-definition shape depends on this module rather than sideways on a
peer feature.

Its dependency floor is deliberate: every version-risky
``prefect.client.schemas`` name -- ``DeploymentCreate``, and whatever else
asking Prefect for its verdict requires -- lives in the private
``_prefect_input_validation`` adapter alone, behind the ``validation.py``
facade, so the value object every consumer touches keeps working across the
whole declared Prefect range.

A ``WorkflowDefinition`` describes one Prefect deployment by data only -- names,
a dotted ``module``/``function`` import reference, and execution settings. It
holds no callable, resolves nothing at construction, and judges no value's
validity: that is Prefect's call, made in ``validation.py``.
"""

from __future__ import annotations

import importlib
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from prefect.flows import Flow

_KEY_SEPARATOR: str = "/"
"""Separator rendering a definition's key as ``flow_name/deployment_name``."""

_KEY_FORMAT: str = "flow_name/deployment_name"
"""The key convention as diagnostics state it. Every message that names the
convention renders this."""


def _not_a_flow_message(*, module: str, function: str, resolved: object) -> str:
    """Describe a reference that resolved to something other than a Prefect flow.

    Shared by :meth:`WorkflowDefinition.load`, which raises it prefixed with the
    definition's key, and by aggregate validation, which reports it unprefixed
    as a validation message. One formatter is why the direct-load and
    aggregate-validation wordings for the same fact cannot drift apart.

    Args:
        module: Dotted module path the reference named.
        function: Attribute name read off that module.
        resolved: Whatever the reference resolved to instead of a flow; only its
            type is rendered.

    Returns:
        The diagnostic, with no leading key prefix -- a caller that wants one
        adds it.
    """
    return (
        f"{module}:{function} is not a Prefect flow -- resolved a "
        f"{type(resolved).__name__} instead (is the @flow decorator missing?)"
    )


@dataclass(frozen=True, kw_only=True, slots=True, init=False)
class WorkflowDefinition:
    """One Prefect workflow, described entirely by data.

    Immutable (assignment raises :exc:`dataclasses.FrozenInstanceError`) and
    keyword-only: the flow and deployment names are both bare strings, so
    positional construction would invite silent identity mixups.

    The declared annotations below are the honest *attribute* types -- what a
    consumer reads back off an instance. The ergonomic parameter types live on
    :meth:`__init__`, which normalizes and then freezes the values.

    Attributes:
        flow_name: Declared Prefect flow name, compared against the resolved
            ``Flow.name`` during validation. Must not contain ``/``.
        module: Dotted module path of the import reference, used only by
            :meth:`load` and validation.
        function: Attribute name of the flow object inside ``module``.
        deployment_name: Deployment name; always a ``str`` after construction,
            equal to ``flow_name`` when the caller supplied none. Must not
            contain ``/``.
        tags: Deployment tags, always a tuple, empty when none were given.
        cron: Optional cron schedule. Validity is Prefect's judgment, not this
            library's.
        concurrency_limit: Optional deployment concurrency limit. Not
            range-checked here -- Prefect's own deployment-input contract
            decides what it accepts.
        collision_strategy: Optional concurrency collision strategy. Prefect's
            ``ConcurrencyLimitStrategy`` members are ``str`` instances, so they
            pass through unchanged; at the installed Prefect version the valid
            strategies are ``"ENQUEUE"`` and ``"CANCEL_NEW"``, and that enum --
            ``prefect.client.schemas.objects.ConcurrencyLimitStrategy`` -- not
            this docstring, is the authoritative set.
        entrypoint: Optional explicit source entrypoint, distinct from the
            ``module``/``function`` import reference. It appears only in
            deployment-creation input and is never used for resolution.

    Example:
        Data only -- constructing this imports nothing from ``example_app``::

            from opsmill_prefect_extras.workflows import WorkflowDefinition

            INVENTORY_REFRESH = WorkflowDefinition(
                flow_name="inventory-refresh",
                deployment_name="scheduled",
                module="example_app.inventory.flows",
                function="refresh_inventory",
                tags=("inventory",),
                cron="0 2 * * *",
                concurrency_limit=1,
                collision_strategy="CANCEL_NEW",
            )

            INVENTORY_REFRESH.key                    # "inventory-refresh/scheduled"
            INVENTORY_REFRESH.to_deployment_input()  # plain-data payload
            INVENTORY_REFRESH.load()                 # imports the named module
    """

    flow_name: str
    module: str
    function: str
    deployment_name: str
    tags: tuple[str, ...]
    cron: str | None = None
    concurrency_limit: int | None = None
    collision_strategy: str | None = None
    entrypoint: str | None = None

    def __init__(
        self,
        *,
        flow_name: str,
        module: str,
        function: str,
        deployment_name: str | None = None,
        tags: Iterable[str] = (),
        cron: str | None = None,
        concurrency_limit: int | None = None,
        collision_strategy: str | None = None,
        entrypoint: str | None = None,
    ) -> None:
        """Normalize the ergonomic arguments, then freeze them.

        Performs no resolution, no import of ``module``, and no validation of
        any value's *validity* -- only the two shape checks below, which
        protect this library's own signature and key grammar.

        Args:
            flow_name: Declared Prefect flow name.
            module: Dotted module path holding the flow object.
            function: Attribute name of the flow object inside ``module``.
            deployment_name: Deployment name; ``None`` means "use
                ``flow_name``".
            tags: Any iterable of tags, normalized to a tuple. A bare ``str``
                or ``bytes`` is refused rather than character-split.
            cron: Optional cron schedule.
            concurrency_limit: Optional deployment concurrency limit.
            collision_strategy: Optional collision strategy -- at the
                installed Prefect version, ``"ENQUEUE"`` or ``"CANCEL_NEW"``
                (authoritative set: Prefect's ``ConcurrencyLimitStrategy``).
            entrypoint: Optional explicit source entrypoint.

        Raises:
            TypeError: If ``tags`` is a bare ``str`` or ``bytes``, which would
                otherwise be split into one tag per character.
            ValueError: If ``flow_name`` or the resolved ``deployment_name``
                contains ``/``, the separator this library renders keys with.
        """
        if isinstance(tags, (str, bytes)):
            rendered = f'"{tags}"' if isinstance(tags, str) else repr(tags)
            raise TypeError(
                f"tags must be an iterable of tag strings, not a bare "
                f"{type(tags).__name__} (it would be split into one tag per "
                f"character) -- did you mean tags=({rendered},)?"
            )

        resolved_deployment_name = (
            flow_name if deployment_name is None else deployment_name
        )
        for name, value in (
            ("flow_name", flow_name),
            ("deployment_name", resolved_deployment_name),
        ):
            if _KEY_SEPARATOR in value:
                raise ValueError(
                    f"{name} must not contain {_KEY_SEPARATOR!r}: {value!r} -- a "
                    f"definition's key is rendered as {_KEY_FORMAT}, "
                    f"so a separator inside either name would make keys "
                    f"ambiguous (Prefect bans it in both names too)"
                )

        # object.__setattr__ is how a frozen dataclass populates its fields;
        # going through it keeps the frozen/slots semantics intact.
        object.__setattr__(self, "flow_name", flow_name)
        object.__setattr__(self, "module", module)
        object.__setattr__(self, "function", function)
        object.__setattr__(self, "deployment_name", resolved_deployment_name)
        object.__setattr__(self, "tags", tuple(tags))
        object.__setattr__(self, "cron", cron)
        object.__setattr__(self, "concurrency_limit", concurrency_limit)
        object.__setattr__(self, "collision_strategy", collision_strategy)
        object.__setattr__(self, "entrypoint", entrypoint)

    @property
    def key(self) -> str:
        """The deployment identity, as ``flow_name/deployment_name``.

        Returns:
            The rendered key. Derived, never caller-supplied; injective,
            because ``/`` is rejected in both name halves at construction.
        """
        return f"{self.flow_name}{_KEY_SEPARATOR}{self.deployment_name}"

    # Flow[Any, Any]: the resolved flow's signature is the consumer's, not ours.
    def load(self) -> Flow[Any, Any]:
        """Resolve the implementing Prefect flow, fresh on every call.

        Imports ``module``, reads ``function`` off it, and confirms the result
        really is a Prefect flow. Nothing is cached, so the answer always
        reflects the current state of the process -- a rename or reload between
        calls is seen by the next one.

        Returns:
            The resolved :class:`prefect.flows.Flow` object.

        Raises:
            ModuleNotFoundError: If ``module`` does not exist. Propagated
                unwrapped.
            Exception: Whatever ``module`` itself raises at import time,
                propagated unwrapped and never rewrapped as an
                :exc:`ImportError`.
            AttributeError: If ``module`` has no ``function`` attribute.
            TypeError: If the resolved object is not a Prefect flow. The
                message names this definition's key and the object's type.
        """
        module = importlib.import_module(self.module)
        resolved = getattr(module, self.function)
        if not isinstance(resolved, Flow):
            # Prefixed with the key, which a direct call has and an aggregate
            # report supplies for itself; the diagnostic itself is shared.
            detail = _not_a_flow_message(
                module=self.module, function=self.function, resolved=resolved
            )
            raise TypeError(f"{self.key}: {detail}")
        return resolved

    # dict[str, Any]: a deployment-creation payload is heterogeneous by nature.
    def to_deployment_input(self) -> dict[str, Any]:
        """Render this definition as deployment-creation input.

        The keys are Prefect's own: parameter names of
        ``PrefectClient.create_deployment`` / fields of
        ``prefect.client.schemas.actions.DeploymentCreate``. Values are plain
        data (strings, ints, lists, dicts -- no Prefect model instances), which
        Prefect coerces into its typed schema on receipt, so the payload splats
        into ``create_deployment`` alongside the server-assigned ``flow_id``.

        ``name`` and ``tags`` are always present; every optional setting
        appears only when it was supplied, because this library invents no
        defaults for the ones that were not. ``flow_name`` is deliberately
        absent -- ``create_deployment`` has no such parameter, and the
        definition itself travels with the payload as the single handle. The
        server-assigned ``flow_id`` is likewise absent.

        Returns:
            A fresh ``dict`` on every call, safe for the caller to mutate.

        Raises:
            Nothing. Rendering neither validates nor imports; invalid values
            surface through validation, or when Prefect itself rejects the
            payload downstream.
        """
        payload: dict[str, Any] = {
            "name": self.deployment_name,
            "tags": list(self.tags),
        }
        if self.entrypoint is not None:
            payload["entrypoint"] = self.entrypoint
        if self.cron is not None:
            payload["schedules"] = [{"schedule": {"cron": self.cron}}]
        if self.concurrency_limit is not None:
            payload["concurrency_limit"] = self.concurrency_limit
        if self.collision_strategy is not None:
            payload["concurrency_options"] = {
                "collision_strategy": self.collision_strategy
            }
        return payload
