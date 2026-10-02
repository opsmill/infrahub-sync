"""Pure deterministic NetBox tier data, with endpoint-local ordinal references.

Ordinals are resolved to database IDs by the API writer, never assumed to be real IDs.
Tier S retains the original qualification dataset, including its skip cases.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

KINDS = (
    "extras/tags",
    "dcim/sites",
    "dcim/racks",
    "dcim/manufacturers",
    "dcim/platforms",
    "dcim/device-types",
    "dcim/devices",
    "dcim/interfaces",
    "circuits/providers",
    "circuits/circuits",
    "ipam/rirs",
    "ipam/aggregates",
    "ipam/route-targets",
    "ipam/vrfs",
    "ipam/vlan-groups",
    "ipam/vlans",
    "ipam/prefixes",
    "ipam/ip-addresses",
)
TIER_COUNTS = dict(
    zip(
        ("S", "M", "L"),
        (
            dict(zip(KINDS, (10, 3, 6, 2, 3, 4, 40, 320, 2, 4, 2, 4, 4, 2, 2, 12, 20, 120), strict=True)),
            dict(zip(KINDS, (40, 10, 30, 4, 6, 12, 800, 6400, 6, 40, 3, 12, 12, 6, 6, 120, 300, 2400), strict=True)),
            dict(
                zip(
                    KINDS,
                    (100, 25, 150, 8, 12, 30, 7000, 56000, 10, 100, 4, 20, 24, 12, 20, 800, 2500, 21000),
                    strict=True,
                )
            ),
        ),
        strict=True,
    )
)
SKIP_COUNTS = dict(
    zip(
        ("S", "M", "L"),
        (
            {"dcim/devices": 2, "dcim/interfaces": 4, "ipam/vlans": 2},
            {"dcim/devices": 8, "dcim/interfaces": 16, "ipam/vlans": 12},
            {"dcim/devices": 35, "dcim/interfaces": 70, "ipam/vlans": 40},
        ),
        strict=True,
    )
)
FOUNDATION_COUNTS = {"dcim/device-roles": 1, "circuits/circuit-types": 1}

# Every ordinal-valued API field has one destination endpoint.
RELATIONS = {
    "tags": "extras/tags",
    "site": "dcim/sites",
    "rack": "dcim/racks",
    "manufacturer": "dcim/manufacturers",
    "platform": "dcim/platforms",
    "device_type": "dcim/device-types",
    "role": "dcim/device-roles",
    "device": "dcim/devices",
    "lag": "dcim/interfaces",
    "assigned_object_id": "dcim/interfaces",
    "untagged_vlan": "ipam/vlans",
    "tagged_vlans": "ipam/vlans",
    "group": "ipam/vlan-groups",
    "rir": "ipam/rirs",
    "import_targets": "ipam/route-targets",
    "export_targets": "ipam/route-targets",
    "vrf": "ipam/vrfs",
    "provider": "circuits/providers",
}


def validate_tier(tier: str) -> None:
    """Reject invalid tiers before any database operation."""
    if tier not in TIER_COUNTS:
        msg = "tier must be S, M, or L"
        raise ValueError(msg)


@dataclass
class Row:
    """A seed row with an endpoint-local ordinal and writable API fields."""

    id: int
    fields: dict[str, Any]

    @property
    def name(self) -> str | None:
        """Return the name, including null names for deliberate skip cases."""
        return self.fields.get("name")

    @property
    def slug(self) -> str:
        """Return the slug used by the foundational seed objects."""
        return self.fields["slug"]


def _expand_foundations(kind: str, payloads: list[dict[str, Any]], tier: str) -> list[dict[str, Any]]:
    """Extend fixed S foundations to the selected tier without randomness."""
    sizes = TIER_COUNTS[tier]
    if tier == "S":
        return payloads
    # Keep the original foundations, then continue their fixed numbering.
    for i in range(len(payloads), sizes[kind]):
        payload = dict(payloads[i % len(payloads)])
        for field in ("name", "slug", "model", "part_number"):
            if field in payload:
                payload[field] = f"{kind.rsplit('/', maxsplit=1)[-1]}-{i + 1:03d}"
        if kind == "ipam/aggregates":
            payload["prefix"] = f"10.{i}.0.0/16"
        if kind == "ipam/vrfs":
            payload["rd"] = f"65000:{(i + 1) * 100}"
            pool = sizes["ipam/route-targets"]
            payload["import_targets"] = [i * 2 % pool + 1, (i * 2 + 1) % pool + 1]
            payload["export_targets"] = [(i * 2 + 2) % pool + 1, (i * 2 + 3) % pool + 1]
        if kind in {"dcim/device-types", "dcim/platforms"}:
            payload["manufacturer"] = i % sizes["dcim/manufacturers"] + 1
        if kind == "dcim/device-types":
            payload.update(u_height=1, weight=3)
        payloads.append(payload)
    if kind == "dcim/platforms":
        for i, payload in enumerate(payloads):
            payload["manufacturer"] = i % sizes["dcim/manufacturers"] + 1
    if kind == "ipam/aggregates":
        for i, payload in enumerate(payloads):
            payload["prefix"] = f"10.{i}.0.0/16"
    return payloads


def build_dataset(tier: str = "S") -> dict[str, list[Row]]:
    """Build the entire tier without network access or randomness."""
    validate_tier(tier)
    sizes, skips = TIER_COUNTS[tier], SKIP_COUNTS[tier]
    data: dict[str, list[Row]] = {}
    racked = sizes["dcim/devices"] * 3 // 4
    per_rack = racked // sizes["dcim/racks"]

    def add(kind: str, payloads: list[dict[str, Any]]) -> list[Row]:
        """Append payloads with endpoint-local ordinal IDs."""
        rows = data.setdefault(kind, [])
        new = [Row(len(rows) + i + 1, payload) for i, payload in enumerate(payloads)]
        rows.extend(new)
        return new

    def expand(kind: str, payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Expand a foundational endpoint to the selected tier size."""
        return _expand_foundations(kind, payloads, tier)

    # --- tags ---------------------------------------------------------------
    tags = add(
        "extras/tags",
        [
            {"name": f"tag-{i:02d}", "slug": f"tag-{i:02d}", "description": f"seed tag {i}"}
            for i in range(1, sizes["extras/tags"] + 1)
        ],
    )
    tag_ids = [t.id for t in tags]

    def tag_pair(i: int) -> list[int]:
        """Select two consecutive tags from the fixed cyclic pool."""
        return [tag_ids[i % len(tag_ids)], tag_ids[(i + 1) % len(tag_ids)]]

    # --- sites / racks ------------------------------------------------------
    sites = add(
        "dcim/sites",
        [
            {
                "name": f"site-{c}",
                "slug": f"site-{c}",
                "status": "active",
                "facility": f"FAC-{c.upper()}",
                "physical_address": f"1 {c} street",
                "time_zone": "UTC",
                "tags": tag_pair(i),
            }
            for i, c in enumerate([chr(97 + i) for i in range(sizes["dcim/sites"])])
        ],
    )
    site_ids = [s.id for s in sites]

    racks = add(
        "dcim/racks",
        [
            {
                "name": f"rack-{s.slug}-{n}",
                "site": s.id,
                "u_height": 42,
                "status": "active",
                "serial": f"RSER-{s.slug}-{n}",
                "asset_tag": f"RAT-{s.slug}-{n}",
                "facility_id": f"RF-{s.slug}-{n}",
                "tags": tag_pair(i),
            }
            for i, s in enumerate(sites)
            for n in range(1, sizes["dcim/racks"] // len(sites) + 1)
        ],
    )
    rack_ids = [r.id for r in racks]

    # --- manufacturers / platforms / device types / role ---------------------
    mfrs = add(
        "dcim/manufacturers",
        expand(
            "dcim/manufacturers",
            [
                {"name": "acme", "slug": "acme", "description": "seed mfr", "tags": tag_pair(0)},
                {"name": "globex", "slug": "globex", "description": "seed mfr", "tags": tag_pair(1)},
            ],
        ),
    )

    platforms = add(
        "dcim/platforms",
        expand(
            "dcim/platforms",
            [
                {"name": "acme-os", "slug": "acme-os", "manufacturer": mfrs[0].id},
                {"name": "acme-lite", "slug": "acme-lite", "manufacturer": mfrs[0].id},
                {"name": "globex-os", "slug": "globex-os", "manufacturer": mfrs[1].id},
            ],
        ),
    )

    # Fractional u_height / weight exercise the ceil transforms.
    dtypes = add(
        "dcim/device-types",
        expand(
            "dcim/device-types",
            [
                {
                    "model": "acme-router-1",
                    "slug": "acme-router-1",
                    "manufacturer": mfrs[0].id,
                    "u_height": 1.5,
                    "part_number": "AR1",
                    "is_full_depth": True,
                    "weight": 4.2,
                    "weight_unit": "kg",
                },
                {
                    "model": "acme-switch-1",
                    "slug": "acme-switch-1",
                    "manufacturer": mfrs[0].id,
                    "u_height": 1,
                    "part_number": "AS1",
                    "is_full_depth": False,
                    "weight": 3,
                    "weight_unit": "kg",
                },
                {
                    "model": "globex-router-1",
                    "slug": "globex-router-1",
                    "manufacturer": mfrs[1].id,
                    "u_height": 2.5,
                    "part_number": "GR1",
                    "is_full_depth": True,
                    "weight": 7.7,
                    "weight_unit": "kg",
                },
                {
                    "model": "globex-switch-1",
                    "slug": "globex-switch-1",
                    "manufacturer": mfrs[1].id,
                    "u_height": 1,
                    "part_number": "GS1",
                    "is_full_depth": False,
                    "weight": 2,
                    "weight_unit": "kg",
                },
            ],
        ),
    )

    role = add("dcim/device-roles", [{"name": "qualification", "slug": "qualification", "color": "2196f3"}])[0]

    # --- VLAN groups / VLANs (before interfaces need them) -------------------
    vgroups = add(
        "ipam/vlan-groups",
        expand(
            "ipam/vlan-groups",
            [
                {"name": "grp-a", "slug": "grp-a", "description": "seed"},
                {"name": "grp-b", "slug": "grp-b", "description": "seed"},
            ],
        ),
    )

    grouped_vlans = add(
        "ipam/vlans",
        [
            {
                "name": f"vlan-{100 + i}",
                "vid": 100 + i,
                "status": "active",
                "group": vgroups[i % len(vgroups)].id,
                "description": "seed grouped",
            }
            for i in range(sizes["ipam/vlans"])
        ],
    )
    add(
        "ipam/vlans",
        [
            {"name": f"vlan-nogroup-{i}", "vid": 900 + i, "status": "active", "description": "seed skip-case"}
            for i in range(skips["ipam/vlans"])
        ],
    )

    # --- devices --------------------------------------------------------------
    device_payloads = []
    for d in range(1, sizes["dcim/devices"] + 1):
        dtype_idx = (d - 1) % len(dtypes)
        if tier != "S" and d <= racked:
            dtype_idx = [i for i, row in enumerate(dtypes) if row.fields["u_height"] == 1][(d - 1) % (len(dtypes) - 2)]
        # platform manufacturer must match the device type's manufacturer (NetBox constraint)
        platform = platforms[(d - 1) % 2] if dtype_idx < 2 else platforms[2]
        if tier != "S":
            platform = next(
                p for p in platforms if p.fields["manufacturer"] == dtypes[dtype_idx].fields["manufacturer"]
            )
        payload = {
            "name": f"dev-{d:02d}",
            "role": role.id,
            "status": "active",
            "device_type": dtypes[dtype_idx].id,
            "platform": platform.id,
            "serial": f"DSER-{d:04d}",
            "description": f"seed device {d}",
            "tags": tag_pair(d),
        }
        if d <= racked:  # S/M/L: 5/20/35 devices per rack, distinct positions, front face
            rack_idx = (d - 1) // per_rack
            payload["site"] = racks[rack_idx].fields["site"]
            payload["rack"] = rack_ids[rack_idx]
            # S uses 5U spacing for fractional-height types; M/L use 1U types and spacing.
            payload["position"] = (5 if tier == "S" else 1) * ((d - 1) % per_rack) + 1
            payload["face"] = "front"
        else:  # unracked: site-only location path
            payload["site"] = site_ids[(d - racked - 1) % len(sites)]
        device_payloads.append(payload)
    # skip-case: unnamed devices (config filters name is_not_empty)
    device_payloads += [
        {
            "name": None,
            "role": role.id,
            "status": "active",
            "device_type": dtypes[0].id,
            "site": site_ids[0],
            "description": f"seed unnamed {i}",
        }
        for i in range(skips["dcim/devices"])
    ]
    devices = add("dcim/devices", device_payloads)
    named = [d for d in devices if d.name]
    unnamed = [d for d in devices if not d.name]

    # --- interfaces -----------------------------------------------------------
    iface_payloads = []
    interface_modes = ("access", "tagged", "tagged-all")
    for device_index, d in enumerate(named):
        for n in range(6):
            payload: dict[str, Any] = {
                "device": d.id,
                "name": f"eth{n}",
                "type": "1000base-t",
                "description": f"phys {n}",
                "mtu": 1500,
            }
            if n < 3:
                payload["mode"] = interface_modes[n]
                if n == 0:
                    payload["untagged_vlan"] = grouped_vlans[device_index % len(grouped_vlans)].id
                elif n == 1:
                    payload["tagged_vlans"] = [
                        grouped_vlans[(device_index + 1) % len(grouped_vlans)].id,
                        grouped_vlans[(device_index + 2) % len(grouped_vlans)].id,
                    ]
            iface_payloads.append(payload)
        lag_mode = interface_modes[device_index % 3]
        lag_payload: dict[str, Any] = {
            "device": d.id,
            "name": "Po1",
            "type": "lag",
            "description": "lag",
            "mode": lag_mode,
        }
        if lag_mode == "access":
            lag_payload["untagged_vlan"] = grouped_vlans[device_index % len(grouped_vlans)].id
        elif lag_mode == "tagged":
            lag_payload["tagged_vlans"] = [grouped_vlans[(device_index + 3) % len(grouped_vlans)].id]
        iface_payloads.append(lag_payload)
        uv = grouped_vlans[d.id % len(grouped_vlans)]
        tv = [grouped_vlans[(d.id + 1) % len(grouped_vlans)].id, grouped_vlans[(d.id + 2) % len(grouped_vlans)].id]
        virtual_mode = interface_modes[device_index % 3]
        iface_payloads.append(
            {
                "device": d.id,
                "name": "vlan100",
                "type": "virtual",
                "mode": virtual_mode,
                "untagged_vlan": uv.id if virtual_mode in {"access", "tagged"} else None,
                "tagged_vlans": tv if virtual_mode == "tagged" else [],
                "description": "virt",
            }
        )
    for d in unnamed:  # skip-case interfaces (device.name empty)
        iface_payloads += [{"device": d.id, "name": f"eth{n}", "type": "1000base-t"} for n in range(2)]
    interfaces = add("dcim/interfaces", iface_payloads)

    by_dev_name = {}
    for i in interfaces:
        by_dev_name[i.fields["device"], i.name] = i
    # LAG membership: eth4/eth5 join Po1
    for d in named:
        po1 = by_dev_name[d.id, "Po1"]
        for n in (4, 5):
            eth = by_dev_name[d.id, f"eth{n}"]
            eth.fields["lag"] = po1.id

    # --- IPAM: RIRs, aggregates, RTs, VRFs, prefixes, IPs ---------------------
    rirs = add(
        "ipam/rirs",
        expand(
            "ipam/rirs",
            [
                {
                    "name": "rir-private",
                    "slug": "rir-private",
                    "is_private": True,
                    "description": "seed",
                    "tags": tag_pair(2),
                },
                {
                    "name": "rir-public",
                    "slug": "rir-public",
                    "is_private": False,
                    "description": "seed",
                    "tags": tag_pair(3),
                },
            ],
        ),
    )

    add(
        "ipam/aggregates",
        expand(
            "ipam/aggregates",
            [
                {"prefix": "10.0.0.0/8", "rir": rirs[0].id, "date_added": "2026-01-01", "description": "seed"},
                {"prefix": "172.16.0.0/12", "rir": rirs[0].id, "date_added": "2026-01-01", "description": "seed"},
                {"prefix": "192.168.0.0/16", "rir": rirs[0].id, "date_added": "2026-01-01", "description": "seed"},
                {"prefix": "100.64.0.0/10", "rir": rirs[1].id, "date_added": "2026-01-01", "description": "seed"},
            ],
        ),
    )

    rts = add(
        "ipam/route-targets",
        [{"name": f"65000:{i}", "description": f"seed rt {i}"} for i in range(1, sizes["ipam/route-targets"] + 1)],
    )

    vrfs = add(
        "ipam/vrfs",
        expand(
            "ipam/vrfs",
            [
                {
                    "name": "vrf-red",
                    "rd": "65000:100",
                    "enforce_unique": True,
                    "import_targets": [rts[0].id, rts[1].id],
                    "export_targets": [rts[2].id, rts[3].id],
                    "description": "seed",
                },
                {
                    "name": "vrf-blue",
                    "rd": "65000:200",
                    "enforce_unique": True,
                    "import_targets": [rts[2].id, rts[3].id],
                    "export_targets": [rts[0].id, rts[1].id],
                    "description": "seed",
                },
            ],
        ),
    )

    if tier == "S":
        prefix_payloads = [
            {"prefix": "10.0.0.0/8", "status": "container", "vrf": vrfs[0].id, "description": "seed container"},
            {"prefix": "192.168.0.0/16", "status": "container", "vrf": vrfs[1].id, "description": "seed container"},
        ]
        prefix_payloads += [
            {"prefix": f"10.1.{i}.0/24", "status": "active", "vrf": vrfs[i % 2].id, "description": "seed vrf prefix"}
            for i in range(10)
        ]
        prefix_payloads += [
            {
                "prefix": f"192.168.{i}.0/24",
                "status": "active",
                "vrf": vrfs[i % 2].id,
                "description": "seed formerly-global prefix",
            }
            for i in range(8)
        ]
    else:
        prefix_payloads = [
            {
                "prefix": f"10.{i // 250}.{i % 250}.0/24",
                "status": "container" if i < round(sizes["ipam/prefixes"] * 0.07) else "active",
                "vrf": vrfs[i % len(vrfs)].id,
                "description": "seed vrf prefix",
            }
            for i in range(sizes["ipam/prefixes"])
        ]
    add("ipam/prefixes", prefix_payloads)

    ip_payloads = []
    if tier == "S":
        for idx, d in enumerate(named, start=1):
            eth0 = by_dev_name[d.id, "eth0"]
            vlan_if = by_dev_name[d.id, "vlan100"]
            ip_payloads.extend(
                [
                    {
                        "address": f"10.1.{idx}.1/24",
                        "status": "active",
                        "vrf": vrfs[idx % 2].id,
                        "description": "seed eth0",
                        "assigned_object_type": "dcim.interface",
                        "assigned_object_id": eth0.id,
                    },
                    {
                        "address": f"10.2.{idx}.1/24",
                        "status": "active",
                        "vrf": vrfs[idx % 2].id,
                        "description": "seed vlan100",
                        "assigned_object_type": "dcim.interface",
                        "assigned_object_id": vlan_if.id,
                    },
                    {
                        "address": f"10.3.{idx}.1/24",
                        "status": "active",
                        "vrf": vrfs[idx % 2].id,
                        "description": "seed loose",
                    },
                ]
            )
    else:
        for i, device in enumerate(named):
            for host in (1, 2, 3):
                payload: dict[str, Any] = {
                    "address": f"10.{i // 250}.{i % 250}.{host}/24",
                    "status": "active",
                    "vrf": vrfs[i % len(vrfs)].id,
                    "description": ("seed eth0", "seed vlan100", "seed loose")[host - 1],
                }
                if host < 3:
                    payload.update(
                        assigned_object_type="dcim.interface",
                        assigned_object_id=by_dev_name[device.id, "eth0" if host == 1 else "vlan100"].id,
                    )
                ip_payloads.append(payload)
    add("ipam/ip-addresses", ip_payloads)

    # Deliberately leave every device without a primary IP: an ordinary null
    # cardinality-one relationship the saved-plan test relies on being absent.

    # --- circuits ---------------------------------------------------------------
    ctype = add("circuits/circuit-types", [{"name": "transit", "slug": "transit"}])[0]
    providers = add(
        "circuits/providers",
        expand(
            "circuits/providers",
            [
                {"name": "provider-x", "slug": "provider-x", "description": "seed", "tags": tag_pair(4)},
                {"name": "provider-y", "slug": "provider-y", "description": "seed", "tags": tag_pair(5)},
            ],
        ),
    )
    add(
        "circuits/circuits",
        [
            {
                "cid": f"CIR-{i:03d}",
                "provider": providers[i % len(providers)].id,
                "type": ctype.id,
                "status": "active",
                "commit_rate": 1000 * (i + 1),
                "description": "seed",
            }
            for i in range(sizes["circuits/circuits"])
        ],
    )

    if tier != "S":
        for kind, rows in data.items():
            if kind in sizes and kind != "extras/tags":
                for i, row in enumerate(rows):
                    row.fields["tags"] = tag_pair(i)
    for kind, count in sizes.items():
        if len(data[kind]) != count + skips.get(kind, 0):
            msg = f"wrong generated count for {kind}"
            raise ValueError(msg)
    return data
