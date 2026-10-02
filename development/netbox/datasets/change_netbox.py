"""Apply a deterministic 1% mutation to a freshly restored NetBox benchmark tier."""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Any, TypedDict

if __name__ == "__main__" and not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from development.netbox.datasets.netbox_api import NetboxAPI, environment_credentials, relation_kind
from development.netbox.datasets.tier_data import FOUNDATION_COUNTS, KINDS, SKIP_COUNTS, TIER_COUNTS, Row, build_dataset

LEAF_KINDS = ("dcim/interfaces", "ipam/ip-addresses", "ipam/prefixes", "ipam/vlans", "circuits/circuits")
MARKER_SLUG = "benchmark-change-started"


class Change(TypedDict):
    """A direct NetBox mutation with stable identifiers and symbolic relationship fields."""

    action: str
    kind: str
    identifier: str
    fields: dict[str, Any]


def apportion(total: int, weights: dict[str, int]) -> dict[str, int]:
    """Use largest remainders, breaking equal remainders by insertion order."""
    denominator = sum(weights.values())
    quotas = {key: total * value // denominator for key, value in weights.items()}
    order = sorted(weights, key=lambda key: -(total * weights[key] % denominator))
    for key in order[: total - sum(quotas.values())]:
        quotas[key] += 1
    return quotas


def identifier(kind: str, fields: dict[str, Any], data: dict[str, list[Row]]) -> str:
    """Encode the natural key, including parent names where names are not globally unique."""
    if kind == "dcim/interfaces":
        parts = [data["dcim/devices"][fields["device"] - 1].name, fields["name"]]
    elif kind == "dcim/racks":
        parts = [data["dcim/sites"][fields["site"] - 1].name, fields["name"]]
    elif kind == "dcim/device-types":
        parts = [data["dcim/manufacturers"][fields["manufacturer"] - 1].name, fields["model"]]
    elif kind == "ipam/vlans":
        parts = [data["ipam/vlan-groups"][fields["group"] - 1].name, fields["vid"], fields["name"]]
    elif kind in {"ipam/prefixes", "ipam/ip-addresses"}:
        parts = [data["ipam/vrfs"][fields["vrf"] - 1].name, fields.get("prefix", fields.get("address"))]
    else:
        parts = [fields.get("name", fields.get("prefix", fields.get("cid")))]
    return json.dumps(parts, separators=(",", ":"))


def symbolic_fields(kind: str, fields: dict[str, Any], data: dict[str, list[Row]]) -> dict[str, Any]:
    """Express all foreign keys through stable identifiers, independent of database IDs."""
    result = dict(fields)
    for field, value in fields.items():
        target = relation_kind(kind, field)
        if target and value is not None:

            def reference(ordinal: int, target: str = target) -> dict[str, str]:
                """Name an ordinal relationship target without exposing database IDs."""
                return {"kind": target, "identifier": identifier(target, data[target][ordinal - 1].fields, data)}

            result[field] = [reference(i) for i in value] if isinstance(value, list) else reference(value)
    return result


def spaced(rows: list[Row], count: int) -> list[Row]:
    """Choose sorted names with a fixed stride, without repetitions."""
    if count > len(rows):
        msg = "not enough unreferenced leaf objects"
        raise ValueError(msg)
    return [rows[i * len(rows) // count] for i in range(count)]


def eligible(kind: str, data: dict[str, list[Row]]) -> list[Row]:
    """Exclude every deliberate mapping skip case."""
    rows = data[kind]
    if kind == "dcim/devices":
        rows = [row for row in rows if row.name]
    elif kind == "dcim/interfaces":
        rows = [row for row in rows if data["dcim/devices"][row.fields["device"] - 1].name]
    elif kind == "ipam/vlans":
        rows = [row for row in rows if row.fields.get("group")]
    return sorted(rows, key=lambda row: identifier(kind, row.fields, data))


def unreferenced(kind: str, data: dict[str, list[Row]]) -> list[Row]:
    """Select leaves with no inbound reference, preventing cascading count changes."""
    referenced: set[int] = set()
    for source, rows in data.items():
        for row in rows:
            for field, value in row.fields.items():
                if relation_kind(source, field) == kind and value is not None:
                    referenced.update(value if isinstance(value, list) else [value])
    return [
        row
        for row in eligible(kind, data)
        if row.id not in referenced and (kind != "dcim/interfaces" or row.name in {"eth2", "eth3"})
    ]


def update_fields(kind: str, row: Row, index: int, data: dict[str, list[Row]]) -> dict[str, Any]:
    """Rotate mapped attribute and relationship updates for a kind."""
    if kind == "dcim/sites":
        return {"facility": f"changed facility {index}"}
    if kind == "dcim/racks":
        return {"asset_tag": f"changed-asset-{index}"}
    if kind == "dcim/platforms":
        return {"manufacturer": row.fields["manufacturer"] % len(data["dcim/manufacturers"]) + 1}
    if kind == "ipam/ip-addresses" and index % 2 and "assigned_object_id" in row.fields:
        original = data["dcim/interfaces"][row.fields["assigned_object_id"] - 1]
        target = next(
            candidate
            for candidate in data["dcim/interfaces"]
            if candidate.fields["device"] == original.fields["device"] and candidate.name == "eth1"
        )
        return {
            "assigned_object_type": "dcim.interface",
            "assigned_object_id": target.id,
            "description": f"changed address {index}",
        }
    if kind == "ipam/prefixes" and index % 2:
        return {"vrf": row.fields["vrf"] % len(data["ipam/vrfs"]) + 1, "description": f"changed prefix {index}"}
    return {"part_number" if kind == "dcim/device-types" else "description": f"benchmark changed {kind} {index}"}


def create_fields(kind: str, index: int, data: dict[str, list[Row]]) -> dict[str, Any]:
    """Continue fixed numbering for new leaves, attached only to existing parents."""
    count = len(data[kind]) if kind != "ipam/vlans" else sum(bool(row.fields.get("group")) for row in data[kind])
    if kind == "dcim/interfaces":
        devices = eligible("dcim/devices", data)
        return {
            "device": devices[index % len(devices)].id,
            "name": f"eth{6 + index}",
            "type": "1000base-t",
            "description": "benchmark created",
            "mtu": 1500,
        }
    if kind == "ipam/ip-addresses":
        # Continue the next device-address slot, with no assignment to an interface.
        slot, host = divmod(count + index, 3)
        return {
            "address": f"10.{slot // 250}.{slot % 250}.{host + 1}/24",
            "vrf": data["ipam/vrfs"][index % len(data["ipam/vrfs"])].id,
            "status": "active",
            "description": "benchmark created",
        }
    if kind == "ipam/prefixes":
        slot = count + index
        return {
            "prefix": f"10.{slot // 250}.{slot % 250}.0/24",
            "vrf": data["ipam/vrfs"][index % len(data["ipam/vrfs"])].id,
            "status": "active",
            "description": "benchmark created",
        }
    if kind == "ipam/vlans":
        return {
            "name": f"vlan-{100 + count + index}",
            "vid": 100 + count + index,
            "group": data["ipam/vlan-groups"][index % len(data["ipam/vlan-groups"])].id,
            "status": "active",
            "description": "benchmark created",
        }
    return {
        "cid": f"CIR-{count + index:03d}",
        "provider": data["circuits/providers"][index % len(data["circuits/providers"])].id,
        "type": data["circuits/circuit-types"][0].id,
        "status": "active",
        "commit_rate": 1000,
        "description": "benchmark created",
    }


def plan_changes(tier: str) -> list[Change]:
    """Plan exactly 1% of the syncable tier, with leaf-only creates and deletes."""
    data = build_dataset(tier)
    counts = TIER_COUNTS[tier]
    total = (sum(counts.values()) + 50) // 100
    actions = apportion(total, {"update": 70, "create": 20, "delete": 10})
    result: list[Change] = []
    deleted: dict[str, set[int]] = {}
    for kind, count in apportion(
        actions["delete"], {kind: counts[kind] for kind in LEAF_KINDS if unreferenced(kind, data)}
    ).items():
        rows = spaced(unreferenced(kind, data), count)
        deleted[kind] = {row.id for row in rows}
        result.extend(
            Change(action="delete", kind=kind, identifier=identifier(kind, row.fields, data), fields={}) for row in rows
        )
    updates = apportion(actions["update"], counts)
    relationship_count = math.ceil(actions["update"] * 0.3)
    for kind, count in updates.items():
        rows = [row for row in eligible(kind, data) if row.id not in deleted.get(kind, set())]
        relationships: list[Row] = []
        if kind == "dcim/interfaces":
            relationships = spaced(
                [row for row in rows if row.fields["type"] == "virtual" and row.fields.get("untagged_vlan")],
                relationship_count,
            )
            relationship_ids = {row.id for row in relationships}
            rows = [row for row in rows if row.id not in relationship_ids]
        selected = relationships + spaced(rows, count - len(relationships))
        for i, row in enumerate(selected):
            if i < len(relationships):
                current = data["ipam/vlans"][row.fields["untagged_vlan"] - 1]
                group = current.fields["group"]
                vlans = [v for v in eligible("ipam/vlans", data) if v.fields["group"] == group]
                target = vlans[(vlans.index(current) + 1) % len(vlans)]
                fields = {"untagged_vlan": target.id}
            else:
                fields = update_fields(kind, row, i, data)
            result.append(
                Change(
                    action="update",
                    kind=kind,
                    identifier=identifier(kind, row.fields, data),
                    fields=symbolic_fields(kind, fields, data),
                )
            )
    for kind, count in apportion(actions["create"], {kind: counts[kind] for kind in LEAF_KINDS}).items():
        for i in range(count):
            fields = create_fields(kind, i, data)
            result.append(
                Change(
                    action="create",
                    kind=kind,
                    identifier=identifier(kind, fields, data),
                    fields=symbolic_fields(kind, fields, data),
                )
            )
    return result


def expected_text(tier: str, changes: list[Change]) -> str:
    """Serialize stable expected direct mutations for the benchmark runner."""
    return (
        json.dumps(
            {
                "format_version": 1,
                "tier": tier,
                "counts": dict(Counter(c["action"] for c in changes)),
                "changes": changes,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def live_identifier(kind: str, fields: dict[str, Any]) -> str:
    """Read the same natural key from nested NetBox API records."""
    if kind == "dcim/interfaces":
        parts = [fields["device"]["name"], fields["name"]]
    elif kind == "dcim/racks":
        parts = [fields["site"]["name"], fields["name"]]
    elif kind == "dcim/device-types":
        parts = [fields["manufacturer"]["name"], fields["model"]]
    elif kind == "ipam/vlans":
        parts = [fields["group"]["name"], fields["vid"], fields["name"]]
    elif kind in {"ipam/prefixes", "ipam/ip-addresses"}:
        if not fields.get("vrf"):
            msg = f"restore the tier: expected {kind} object has no VRF"
            raise ValueError(msg)
        parts = [fields["vrf"]["name"], fields.get("prefix", fields.get("address"))]
    else:
        parts = [fields.get("name", fields.get("prefix", fields.get("cid")))]
    return json.dumps(parts, separators=(",", ":"))


def resolve_symbols(fields: dict[str, Any], ids: dict[tuple[str, str], int]) -> dict[str, Any]:
    """Resolve symbolic references against the current database snapshot."""

    def resolve(value: Any) -> Any:  # noqa: ANN401 -- nested JSON may contain scalar or relationship values
        """Resolve nested symbolic references to current database IDs."""
        if isinstance(value, dict) and set(value) == {"kind", "identifier"}:
            return ids[value["kind"], value["identifier"]]
        if isinstance(value, list):
            return [resolve(item) for item in value]
        return value

    return {key: resolve(value) for key, value in fields.items()}


def add_update_aliases(changes: list[Change], data: dict[str, list[Row]], ids: dict[tuple[str, str], int]) -> None:
    """On a forced rerun, find updates whose relationship changed their natural key."""
    ordinals = {(kind, identifier(kind, row.fields, data)): row.id for kind in data for row in eligible(kind, data)}
    originals = {
        (kind, identifier(kind, row.fields, data)): row.fields for kind in KINDS for row in eligible(kind, data)
    }
    for change in changes:
        key = change["kind"], change["identifier"]
        if change["action"] != "update" or key in ids:
            continue
        fields = originals[key] | resolve_symbols(change["fields"], ordinals)
        new_key = change["kind"], identifier(change["kind"], fields, data)
        if new_key in ids:
            ids[key] = ids[new_key]


def verify_change_counts(api: NetboxAPI, tier: str, changes: list[Change], *, force: bool) -> None:
    """Require the selected tier, allowing only planned count changes during forced retries."""
    creates = Counter(c["kind"] for c in changes if c["action"] == "create")
    deletes = Counter(c["kind"] for c in changes if c["action"] == "delete")
    for kind, expected in (TIER_COUNTS[tier] | FOUNDATION_COUNTS).items():
        baseline = expected + SKIP_COUNTS[tier].get(kind, 0)
        minimum = baseline - (deletes[kind] if force else 0)
        maximum = baseline + (creates[kind] if force else 0)
        if not minimum <= api.count(kind) <= maximum:
            msg = f"restore tier {tier}: unexpected {kind} count"
            raise ValueError(msg)


def apply_changes(api: NetboxAPI, tier: str, output: Path, *, force: bool = False) -> None:
    """Refuse a second mutation; mark starts so partial failures also require restore."""
    tags = api.all("extras/tags")
    if any(tag["slug"] == MARKER_SLUG for tag in tags) and not force:
        msg = "changes already started; restore the tier first, or use --force"
        raise ValueError(msg)
    data = build_dataset(tier)
    changes = plan_changes(tier)
    verify_change_counts(api, tier, changes, force=force)
    ids: dict[tuple[str, str], int] = {}
    for kind in (*KINDS, "circuits/circuit-types"):
        for row in api.all(kind):
            if kind == "ipam/vlans" and not row.get("group"):
                continue
            ids[kind, live_identifier(kind, row)] = row["id"]
    deleted_keys = {(c["kind"], c["identifier"]) for c in changes if c["action"] == "delete"}
    if force:
        add_update_aliases(changes, data, ids)
    # Resolve the complete plan before the first write. Missing or wrong-tier data is refused.
    for kind in KINDS:
        for row in eligible(kind, data):
            key = kind, identifier(kind, row.fields, data)
            if key not in ids and not (force and key in deleted_keys):
                msg = f"restore tier {tier}: expected {kind} object is missing"
                raise ValueError(msg)
    try:
        prepared = [(change, resolve_symbols(change["fields"], ids)) for change in changes]
        for change, _fields in prepared:
            if change["action"] == "create" and (change["kind"], change["identifier"]) in ids and not force:
                msg = "created object already exists; restore the tier first"
                raise ValueError(msg)
    except KeyError:
        msg = "restore the tier: a relationship target is missing"
        raise ValueError(msg) from None
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_suffix(".partial.json")
    partial.write_text(expected_text(tier, changes), encoding="utf-8")
    output.unlink(missing_ok=True)
    if not any(tag["slug"] == MARKER_SLUG for tag in tags):
        # Slug is absent from the tag mapping; reuse a tag rather than add a syncable object.
        api.request("PATCH", "extras/tags", {"slug": MARKER_SLUG}, suffix=f"{tags[0]['id']}/")
    for change, fields in prepared:
        kind = change["kind"]
        key = kind, change["identifier"]
        if change["action"] == "create":
            if key not in ids:
                api.request("POST", kind, fields)
        elif change["action"] == "delete" and key not in ids and force:
            continue
        else:
            suffix = f"{ids[kind, change['identifier']]}/"
            api.request(
                "PATCH" if change["action"] == "update" else "DELETE",
                kind,
                fields if change["action"] == "update" else None,
                suffix=suffix,
            )
    partial.replace(output)


def main() -> None:
    """Apply changes using environment credentials and write the expected-file contract."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tier", choices=("S", "M", "L"), required=True)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = args.output or Path(__file__).resolve().parents[3] / ".netbox" / "changes" / f"{args.tier}.expected.json"
    try:
        url, token = environment_credentials()
        with NetboxAPI(url, token) as api:
            apply_changes(api, args.tier, output, force=args.force)
    except (ValueError, RuntimeError) as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()
