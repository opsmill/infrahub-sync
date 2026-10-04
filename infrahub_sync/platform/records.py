"""Sync's configurations, versions, approvals, and run mirrors in Infrahub.

Users create and change configurations in Infrahub; Sync records a version when a
run starts on content no version holds yet, and never changes a version afterwards.
`InfrahubConfigurations` answers the configuration reads the run code makes, with the
same methods and results as the product store's tables, so the run code is
unchanged by where configurations live.

Run state stays in Sync's own database; `RunMirror` copies each run into a `SyncRun`
node so it appears in Infrahub, best effort. Every record is read and written on the
default branch.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Protocol, cast

import yaml

from infrahub_sync.plan.canonical import canonical_json_bytes
from infrahub_sync.product_store import configs
from infrahub_sync.product_store.models import ConfigurationSummary, ConfigurationVersion, LookupResult

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from contextlib import AbstractContextManager

    from infrahub_sync.product_store.models import ProductRun

logger = logging.getLogger(__name__)
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

CONFIGURATION_KIND = "SyncConfiguration"
VERSION_KIND = "SyncConfigurationVersion"
RUN_KIND = "SyncRun"
APPROVAL_KIND = "SyncApproval"
CONFIGURATION_NOT_FOUND = "configuration-not-found"
VERSION_NOT_FOUND = "configuration-version-not-found"


class RecordsClient(Protocol):
    """The synchronous SDK client methods the records use, always called by keyword."""

    def filters(self, kind: str, *, branch: str | None = None, **kwargs: Any) -> Any:
        """The nodes of `kind` on `branch` matching the filters."""
        raise NotImplementedError

    def create(self, kind: str, *, data: dict | None = None, branch: str | None = None, **kwargs: Any) -> Any:
        """A new, unsaved node of `kind` on `branch`."""
        raise NotImplementedError


class ConfigurationDocumentError(ValueError):
    """A configuration's document is not a YAML or JSON mapping."""


def _value(node: Any, name: str) -> Any:
    return getattr(getattr(node, name), "value", None)


def _timestamp(value: object) -> datetime:
    stamp = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return stamp if stamp.utcoffset() is not None else stamp.replace(tzinfo=timezone.utc)


# A configuration package is a few hundred kilobytes at most; the NetBox example is 27 KiB,
# and expands to about 4,000 YAML nodes.
MAX_DOCUMENT_CHARACTERS = 1_048_576
MAX_EXPANDED_NODES = 1_000_000


def _expanded_size(node: yaml.Node, sizes: dict[int, int]) -> int:
    """How many nodes the document holds once every alias is expanded, counted once per shared node."""
    known = sizes.get(id(node))
    if known is not None:
        return known
    size = 1
    if isinstance(node, yaml.SequenceNode):
        size += sum(_expanded_size(item, sizes) for item in node.value)
    elif isinstance(node, yaml.MappingNode):
        size += sum(_expanded_size(key, sizes) + _expanded_size(value, sizes) for key, value in node.value)
    sizes[id(node)] = min(size, MAX_EXPANDED_NODES + 1)
    return sizes[id(node)]


def parse_document(document: str) -> dict[str, Any]:
    """Read a configuration document, YAML or JSON, as declared content.

    A document anyone with edit rights in Infrahub can write is bounded before it is
    loaded: its size is capped, and so is its size with every YAML alias expanded. An
    alias is shared on load and every later walk of the content visits it again, so a
    few hundred bytes of nested aliases would otherwise cost hours of CPU.
    """
    if len(document) > MAX_DOCUMENT_CHARACTERS:
        msg = f"the configuration document is larger than {MAX_DOCUMENT_CHARACTERS} characters"
        raise ConfigurationDocumentError(msg)
    try:
        root = yaml.compose(document, Loader=yaml.SafeLoader)
        if root is not None and _expanded_size(root, {}) > MAX_EXPANDED_NODES:
            msg = f"the configuration document expands to more than {MAX_EXPANDED_NODES} values through YAML aliases"
            raise ConfigurationDocumentError(msg)
        content = yaml.safe_load(document)
    except yaml.YAMLError:
        msg = "the configuration document is not valid YAML or JSON"
        raise ConfigurationDocumentError(msg) from None
    except RecursionError:
        msg = "the configuration document is nested too deeply"
        raise ConfigurationDocumentError(msg) from None
    if not isinstance(content, dict):
        msg = "the configuration document must be a mapping"
        raise ConfigurationDocumentError(msg)
    return content


class DefaultBranch:
    """The default branch, given or read from the server once on first use."""

    def __init__(self, branch: str | Callable[[], str]) -> None:
        self._source = branch
        self._resolved: str | None = branch if isinstance(branch, str) else None

    def __call__(self) -> str:
        if self._resolved is None:
            self._resolved = cast("Callable[[], str]", self._source)()
        return self._resolved


@dataclass(slots=True)
class InfrahubConfigurations:
    """Configurations and their versions, read and recorded in Infrahub.

    `version_lock` serializes version creation per configuration across every Sync
    process, so two runs that start on the same new content share one version.
    """

    client: RecordsClient
    branch: str | Callable[[], str]
    version_lock: Callable[[str], AbstractContextManager[None]]
    _default: DefaultBranch = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._default = DefaultBranch(self.branch)

    def _configuration(self, config_id: str) -> Any | None:
        found = self.client.filters(kind=CONFIGURATION_KIND, branch=self._default(), name__value=config_id)
        return found[0] if found else None

    def _versions(self, config_id: str, **filters: Any) -> list[Any]:
        return self.client.filters(
            kind=VERSION_KIND, branch=self._default(), configuration__name__value=config_id, **filters
        )

    def _version_record(self, config_id: str, node: Any) -> ConfigurationVersion:
        return ConfigurationVersion(
            config_id=config_id,
            registry_version=int(_value(node, "number")),
            package_checksum=str(_value(node, "checksum")),
            declared_content=json.loads(str(_value(node, "document"))),
            created_at=_timestamp(_value(node, "created_at")),
        )

    def lookup_configuration(self, config_id: str) -> LookupResult[ConfigurationSummary]:
        """The configuration, dated by its first version (undated before one), or a not-found verdict."""
        if self._configuration(config_id) is None:
            return LookupResult(value=None, reason=CONFIGURATION_NOT_FOUND)
        versions = self.list_configuration_versions(config_id)
        created_at = versions[0].created_at if versions else None
        return LookupResult(value=ConfigurationSummary(config_id=config_id, created_at=created_at))

    def lookup_configuration_version(self, config_id: str, registry_version: int) -> LookupResult[ConfigurationVersion]:
        """One recorded version by its number, or a not-found verdict."""
        found = self._versions(config_id, number__value=registry_version)
        if not found:
            return LookupResult(value=None, reason=VERSION_NOT_FOUND)
        return LookupResult(value=self._version_record(config_id, found[0]))

    def list_configurations(self) -> tuple[ConfigurationSummary, ...]:
        """Every configuration, oldest first, then by name."""
        summaries = []
        for node in self.client.filters(kind=CONFIGURATION_KIND, branch=self._default()):
            summary = self.lookup_configuration(str(_value(node, "name"))).value
            if summary is not None:
                summaries.append(summary)
        # Dated configurations first, oldest first, then the ones no run has used, each by name.
        return tuple(
            sorted(
                summaries,
                key=lambda summary: (summary.created_at is None, summary.created_at or _EPOCH, summary.config_id),
            )
        )

    def list_configuration_versions(self, config_id: str) -> tuple[ConfigurationVersion, ...]:
        """Every recorded version of one configuration, by number."""
        records = [self._version_record(config_id, node) for node in self._versions(config_id)]
        return tuple(sorted(records, key=lambda record: record.registry_version))

    def default_branch(self) -> str:
        """The name of Infrahub's default branch, where Sync's records live."""
        return self._default()

    def declared_content(self, config_id: str, branch: str | None = None) -> dict[str, Any] | None:
        """The configuration's current document on a branch, the default branch unless named."""
        found = self.client.filters(kind=CONFIGURATION_KIND, branch=branch or self._default(), name__value=config_id)
        if not found:
            return None
        return parse_document(str(_value(found[0], "document") or ""))

    def current_version(self, config_id: str) -> LookupResult[ConfigurationVersion]:
        """The version holding the configuration's current content, recorded now if none does.

        The content is admitted exactly as a registration admitted it, so invalid content
        is refused with its findings and never becomes a version.
        """
        configuration = self._configuration(config_id)
        if configuration is None:
            return LookupResult(value=None, reason=CONFIGURATION_NOT_FOUND)
        declared = parse_document(str(_value(configuration, "document") or ""))
        package = configs.admit_declared(declared)
        checksum = package.checksum()
        with self.version_lock(config_id):
            existing = self._versions(config_id, checksum__value=checksum)
            if existing:
                return LookupResult(value=self._version_record(config_id, existing[0]))
            numbers = [int(_value(node, "number")) for node in self._versions(config_id)]
            number = max(numbers, default=0) + 1
            created_at = datetime.now(timezone.utc)
            node = self.client.create(
                kind=VERSION_KIND,
                branch=self._default(),
                data={
                    "number": number,
                    "checksum": checksum,
                    "document": canonical_json_bytes(declared).decode("utf-8"),
                    "created_at": created_at.isoformat(),
                    "configuration": configuration.id,
                },
            )
            node.save()
        return LookupResult(
            value=ConfigurationVersion(
                config_id=config_id,
                registry_version=number,
                package_checksum=checksum,
                declared_content=declared,
                created_at=created_at,
            )
        )


@dataclass(slots=True)
class RunMirror:
    """Copies run state from Sync's database into `SyncRun` nodes, best effort."""

    client: RecordsClient
    branch: str | Callable[[], str]
    _default: DefaultBranch = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._default = DefaultBranch(self.branch)

    def mirror(
        self,
        run: ProductRun,
        extra: Mapping[str, Any] | None = None,
        *,
        current: Callable[[], ProductRun | None] | None = None,
    ) -> tuple[str, ...]:
        """Create or update the run's mirror, returning the ids of its `SyncRun` and configuration nodes.

        `current` re-reads the run just before the write, so a slower writer does not put
        back a state a faster one already replaced. Best effort: a failure is logged,
        never raised, and returns no ids.
        """
        try:
            links = self._links(run)
            found = self.client.filters(kind=RUN_KIND, branch=self._default(), run_id__value=run.run_id)
            if current is not None:
                run = current() or run
            data = self._data(run, extra)
            if found:
                node = found[0]
                for name, value in data.items():
                    getattr(node, name).value = value
            else:
                node = self.client.create(kind=RUN_KIND, branch=self._default(), data={**data, **links})
            node.save()
        except Exception as error:  # noqa: BLE001  # pylint: disable=broad-exception-caught
            logger.warning(
                "run %s was not mirrored to Infrahub (%s); Sync's database still holds it",
                run.run_id,
                type(error).__name__,
            )
            return ()
        return tuple(str(identifier) for identifier in (node.id, links.get("configuration")) if identifier)

    @staticmethod
    def _data(run: ProductRun, extra: Mapping[str, Any] | None) -> dict[str, Any]:
        data: dict[str, Any] = {
            "run_id": run.run_id,
            "operation": run.operation,
            "phase": run.phase,
            "outcome": run.outcome,
            "requested_by": run.actor,
            "summary": dict(run.summary or {}),
            "started_at": run.started_at.isoformat(),
            **(extra or {}),
        }
        if run.prefect_executions:
            data["flow_run_id"] = run.prefect_executions[-1].flow_run_id
        if run.finished_at is not None:
            data["finished_at"] = run.finished_at.isoformat()
        return data

    def _links(self, run: ProductRun) -> dict[str, Any]:
        links: dict[str, Any] = {}
        if run.config_id is None:
            return links
        configuration = self.client.filters(kind=CONFIGURATION_KIND, branch=self._default(), name__value=run.config_id)
        if configuration:
            links["configuration"] = configuration[0].id
        if run.registry_version is not None:
            version = self.client.filters(
                kind=VERSION_KIND,
                branch=self._default(),
                configuration__name__value=run.config_id,
                number__value=run.registry_version,
            )
            if version:
                links["version"] = version[0].id
        return links

    def approve(self, run_id: str, *, checksum: str, approved_by: str, reason: str) -> None:
        """Record who approved which plan checksum for a run's write, best effort."""
        try:
            found = self.client.filters(kind=RUN_KIND, branch=self._default(), run_id__value=run_id)
            if not found:
                return
            approval = self.client.create(
                kind=APPROVAL_KIND,
                branch=self._default(),
                data={
                    "approved_checksum": checksum,
                    "approved_by": approved_by,
                    "reason": reason,
                    "run": found[0].id,
                },
            )
            approval.save()
        except Exception as error:  # noqa: BLE001  # pylint: disable=broad-exception-caught
            logger.warning("the approval of run %s was not recorded in Infrahub (%s)", run_id, type(error).__name__)
