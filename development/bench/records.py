"""Benchmark result contracts and mapped count/action checks, independent of services."""

from __future__ import annotations

import ast
import json
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from statistics import median
from typing import TYPE_CHECKING, Any

from development.netbox.datasets.change_netbox import eligible, identifier
from development.netbox.datasets.tier_data import TIER_COUNTS, build_dataset

if TYPE_CHECKING:
    from datetime import datetime
    from pathlib import Path

Actions = dict[str, dict[str, int]]
ACTION_NAMES = ("create", "update", "delete")


def mapped_kinds(configuration: dict[str, Any]) -> dict[str, list[str]]:
    """Read destination kinds from the shipped mapping, deduplicating device filters."""
    result: dict[str, list[str]] = defaultdict(list)
    for entry in configuration["schema_mapping"]:
        endpoint = entry["mapping"].replace(".", "/", 1)
        if entry["name"] not in result[endpoint]:
            result[endpoint].append(entry["name"])
    return dict(result)


def expected_counts(tier: str, mapping: dict[str, list[str]]) -> dict[str, int]:
    """Include both VRF projections, split interface kinds, and the preserved default namespace."""
    counts = {kind: TIER_COUNTS[tier][endpoint] for endpoint, kinds in mapping.items() for kind in kinds}
    devices = TIER_COUNTS[tier]["dcim/devices"]
    counts.update(InterfacePhysical=6 * devices, InterfaceVirtual=devices, InterfaceLag=devices)
    counts["IpamNamespace"] += 1
    return counts


def expected_actions(document: dict[str, Any], mapping: dict[str, list[str]]) -> Actions:
    """Translate mutations to mapped actions, including prefix identity moves."""
    if document["format_version"] != 1:
        msg = "unsupported change file format"
        raise ValueError(msg)
    data = build_dataset(document["tier"])
    interfaces = {
        identifier("dcim/interfaces", row.fields, data): row.fields["type"] for row in eligible("dcim/interfaces", data)
    }
    result: dict[str, Counter[str]] = {action: Counter() for action in ACTION_NAMES}
    for change in document["changes"]:
        endpoint, action = change["kind"], change["action"]
        kinds = mapping[endpoint]
        if endpoint == "dcim/interfaces":
            interface_type = change["fields"].get("type", interfaces.get(change["identifier"]))
            kinds = [{"virtual": "InterfaceVirtual", "lag": "InterfaceLag"}.get(interface_type, "InterfacePhysical")]
        for kind in kinds:
            if endpoint == "ipam/prefixes" and action == "update" and "vrf" in change["fields"]:
                result["delete"][kind] += 1
                result["create"][kind] += 1
            else:
                result[action][kind] += 1
    return {action: dict(counts) for action, counts in result.items()}


def normalized(actions: Actions) -> Actions:
    """Omit zero entries so missing zero kinds and explicit zeros compare equally."""
    return {
        action: {kind: count for kind, count in actions.get(action, {}).items() if count} for action in ACTION_NAMES
    }


def validate_result(  # noqa: PLR0913, PLR0917 -- independent validation inputs
    tier: str,
    scenario: str,
    counts: dict[str, int],
    actions: Actions,
    mapping: dict[str, list[str]],
    expected: Actions | None = None,
) -> None:
    """Reject count or action mismatches before a result may carry a valid time."""
    wanted = expected_counts(tier, mapping)
    if scenario == "changed":
        if expected is None or normalized(actions) != normalized(expected):
            msg = "applied actions differ from the expected change file"
            raise ValueError(msg)
        for kind in wanted:
            wanted[kind] += expected["create"].get(kind, 0) - expected["delete"].get(kind, 0)
    elif scenario == "warm" and any(normalized(actions).values()):
        msg = "warm run applied nonzero actions"
        raise ValueError(msg)
    if counts != wanted:
        msg = "Infrahub counts differ from the mapped tier counts"
        raise ValueError(msg)


def count_delta(before: dict[str, int], after: dict[str, int]) -> Actions:
    """Fallback evidence: net count deltas cannot establish updates or offsetting writes."""
    result: Actions = {action: {} for action in ACTION_NAMES}
    for kind in before.keys() | after.keys():
        delta = after.get(kind, 0) - before.get(kind, 0)
        if delta:
            result["create" if delta > 0 else "delete"][kind] = abs(delta)
    return result


def parse_v2_summary(output: str, kinds: set[str]) -> Actions | None:
    """Read complete count summaries or successful DiffSync action logs from a finished run."""
    result: Actions = {action: {} for action in ACTION_NAMES}
    seen: set[str] = set()
    text = re.sub(r"\x1b\[[0-9;]*m", "", output)
    for line in text.splitlines():
        for kind in kinds:
            if not re.search(rf"(?<!\w){re.escape(kind)}(?!\w)", line):
                continue
            counts = {}
            for action in ACTION_NAMES:
                match = re.search(rf"['\"]?{action}['\"]?\s*[:=]\s*(\d+)", line)
                if match:
                    counts[action] = int(match[1])
            if len(counts) == len(ACTION_NAMES):
                seen.add(kind)
                for action, count in counts.items():
                    result[action][kind] = count
    if seen == kinds:
        return normalized(result)
    return _parse_v2_status_lines(text, kinds)


def _parse_v2_status_lines(text: str, kinds: set[str]) -> Actions | None:
    """Count printed object outcomes only when the release reports a finished sync."""
    # Released v2 emits one status line per changed object, followed by its run footer.
    # Require INFO output and completed sync boundaries before absent actions mean zero.
    if not re.search(r"INFO\s*\|\s*infrahub_sync.cli\s*\|\s*Sync run \S+ at ", text):
        return None
    beginning, completed = text.count("Beginning sync"), text.count("Sync complete")
    tiered_zero = _finished_v2_tiers(text, kinds)
    if beginning != completed or (
        not beginning and "No difference found. Nothing to sync" not in text and not tiered_zero
    ):
        return None
    logged: Actions = {action: {} for action in ACTION_NAMES}
    for line in text.splitlines():
        action = re.search(r"\baction=['\"]?(create|update|delete)['\"]?(?=\s|$)", line)
        kind = re.search(r"\bmodel=['\"]?(\w+)['\"]?(?=\s|$)", line)
        status = re.search(r"\bstatus=['\"]?(\w+)['\"]?(?=\s|$)", line)
        if action and kind and status:
            if status[1] != "success" or kind[1] not in kinds:
                return None
            bucket = logged[action[1]]
            bucket[kind[1]] = bucket.get(kind[1], 0) + 1
    if bool(beginning) != bool(any(logged.values())):
        return None
    return logged


def _finished_v2_tiers(text: str, kinds: set[str]) -> bool:
    """Establish a no-write tiered sync from all mapped tiers and the release footer."""
    footer = re.search(r"INFO\s*\|\s*infrahub_sync.cli\s*\|\s*Sync run \S+ at ", text)
    if footer is None:
        return False
    tiers = list(
        re.finditer(
            r"INFO\s*\|\s*infrahub_sync.potenda\s*\|\s*Sync tier (\d+) \((\d+)\): (\[[^\n]+\])",
            text,
        )
    )
    covered: set[str] = set()
    for index, tier in enumerate(tiers):
        if int(tier[1]) != index or tier.end() > footer.start():
            return False
        try:
            members = ast.literal_eval(tier[3])
        except (SyntaxError, ValueError):
            return False
        if not isinstance(members, list) or not all(isinstance(kind, str) for kind in members):
            return False
        if len(members) != int(tier[2]):
            return False
        covered.update(members)
    return bool(tiers) and kinds <= covered


def stage_seconds(started: datetime, planned: datetime, finished: datetime) -> tuple[float, float]:
    """Split recorded run time at the published plan, including stage overhead."""
    if not started <= planned <= finished:
        msg = "inconsistent run stage timestamps"
        raise ValueError(msg)
    return (planned - started).total_seconds(), (finished - planned).total_seconds()


@dataclass
class ResultRecord:
    """One cell repetition; failed and timed-out cells never expose valid timings."""

    line: str
    version: str
    commit: str
    tier: str
    scenario: str
    variant: str
    repetition: int
    status: str = "failed"
    wall_seconds: float | None = None
    plan_seconds: float | None = None
    apply_seconds: float | None = None
    peak_rss_mb: float | None = None
    netbox_counts: dict[str, int] = field(default_factory=dict)
    infrahub_counts: dict[str, int] = field(default_factory=dict)
    machine: dict[str, Any] = field(default_factory=dict)
    actions: Actions = field(default_factory=dict)
    skipped_deletes: dict[str, int] = field(default_factory=dict)
    action_evidence: str | None = None
    error: str | None = None
    run_id: str | None = None
    infrahub_version: str | None = None
    infrahub_image_id: str | None = None
    infrahub_image_digest: str | None = None

    def append(self, path: Path) -> None:
        """Append exactly one JSON line, clearing times on every invalid result."""
        if self.status not in {"ok", "failed", "timed_out"}:
            msg = "invalid benchmark status"
            raise ValueError(msg)
        if self.status != "ok":
            self.wall_seconds = self.plan_seconds = self.apply_seconds = None
        elif self.wall_seconds is None or self.peak_rss_mb is None:
            msg = "successful result needs time and memory evidence"
            raise ValueError(msg)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(self), sort_keys=True) + "\n")


def medians(path: Path) -> list[dict[str, Any]]:
    """Compare valid medians without pooling versions, commits, or v2 variants."""
    if not path.exists():
        return []
    cells: dict[tuple[str, str], dict[tuple[str, str, str, str], list[float]]] = defaultdict(lambda: defaultdict(list))
    for line in path.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        if record["status"] == "ok" and record["wall_seconds"] is not None:
            identity = record["line"], record["version"], record["commit"], record["variant"]
            cells[record["tier"], record["scenario"]][identity].append(record["wall_seconds"])
    rows = []
    for (tier, scenario), identities in sorted(cells.items()):
        v2 = [identity for identity in identities if identity[0] == "v2"] or [None]
        v3 = [identity for identity in identities if identity[0] == "v3"] or [None]
        for left in v2:
            for right in v3:
                rows.append(  # noqa: PERF401 -- retain the two-line comparison structure
                    {
                        "tier": tier,
                        "scenario": scenario,
                        "variant": left[3] if left else "full",
                        "v2_version": f"{left[1]}@{left[2]}" if left else None,
                        "v3_version": f"{right[1]}@{right[2]}" if right else None,
                        "v2_seconds": median(identities[left]) if left else None,
                        "v3_seconds": median(identities[right]) if right else None,
                    }
                )
    return rows
