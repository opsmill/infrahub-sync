"""Deterministic NetBox seeder for the `seed` dataset.

Seeds a disposable local NetBox instance with a fixed, non-random dataset that the
saved-plan apply integration test
(`tests/integration/test_saved_plan_apply_integration.py`) requires: sites `site-a`,
`site-b`, `site-c`; racks `rack-<site>-<n>`; devices `dev-01`..`dev-40`; tags
`tag-01`..`tag-10`. Safe to run only against a fresh instance — it asserts emptiness
first and never deletes.

Every address below is documentation or RFC 1918 space (RFC 5737, RFC 1918); none of
it is reachable or real.

Usage:
    uv run python development/netbox/datasets/seed_netbox.py --url http://localhost:8082 --token <token>
"""

from __future__ import annotations

import argparse
import json
import sys

import pynetbox


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--token", required=True)
    args = parser.parse_args()

    nb = pynetbox.api(args.url, token=args.token)

    if nb.dcim.sites.count() or nb.dcim.devices.count():
        sys.exit("refusing to seed: instance is not empty")

    counts: dict[str, int] = {}

    # --- tags ---------------------------------------------------------------
    tags = nb.extras.tags.create(
        [{"name": f"tag-{i:02d}", "slug": f"tag-{i:02d}", "description": f"seed tag {i}"} for i in range(1, 11)]
    )
    tag_ids = [t.id for t in tags]
    counts["extras/tags"] = len(tags)

    def tag_pair(i: int) -> list[int]:
        return [tag_ids[i % 10], tag_ids[(i + 1) % 10]]

    # --- sites / racks ------------------------------------------------------
    sites = nb.dcim.sites.create(
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
            for i, c in enumerate(["a", "b", "c"])
        ]
    )
    site_ids = [s.id for s in sites]
    counts["dcim/sites"] = len(sites)

    racks = nb.dcim.racks.create(
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
            for n in (1, 2)
        ]
    )
    rack_ids = [r.id for r in racks]
    counts["dcim/racks"] = len(racks)

    # --- manufacturers / platforms / device types / role ---------------------
    mfrs = nb.dcim.manufacturers.create(
        [
            {"name": "acme", "slug": "acme", "description": "seed mfr", "tags": tag_pair(0)},
            {"name": "globex", "slug": "globex", "description": "seed mfr", "tags": tag_pair(1)},
        ]
    )
    counts["dcim/manufacturers"] = len(mfrs)

    platforms = nb.dcim.platforms.create(
        [
            {"name": "acme-os", "slug": "acme-os", "manufacturer": mfrs[0].id},
            {"name": "acme-lite", "slug": "acme-lite", "manufacturer": mfrs[0].id},
            {"name": "globex-os", "slug": "globex-os", "manufacturer": mfrs[1].id},
        ]
    )
    counts["dcim/platforms"] = len(platforms)

    # Fractional u_height / weight exercise the ceil transforms.
    dtypes = nb.dcim.device_types.create(
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
        ]
    )
    counts["dcim/device-types"] = len(dtypes)

    role = nb.dcim.device_roles.create(name="qualification", slug="qualification", color="2196f3")

    # --- VLAN groups / VLANs (before interfaces need them) -------------------
    vgroups = nb.ipam.vlan_groups.create(
        [
            {"name": "grp-a", "slug": "grp-a", "description": "seed"},
            {"name": "grp-b", "slug": "grp-b", "description": "seed"},
        ]
    )
    counts["ipam/vlan-groups"] = len(vgroups)

    grouped_vlans = nb.ipam.vlans.create(
        [
            {
                "name": f"vlan-{100 + i}",
                "vid": 100 + i,
                "status": "active",
                "group": vgroups[i % 2].id,
                "description": "seed grouped",
            }
            for i in range(12)
        ]
    )
    ungrouped = nb.ipam.vlans.create(
        [
            {"name": f"vlan-nogroup-{i}", "vid": 900 + i, "status": "active", "description": "seed skip-case"}
            for i in range(2)
        ]
    )
    counts["ipam/vlans"] = len(grouped_vlans) + len(ungrouped)

    # --- devices --------------------------------------------------------------
    device_payloads = []
    for d in range(1, 41):
        dtype_idx = (d - 1) % 4
        # platform manufacturer must match the device type's manufacturer (NetBox constraint)
        platform = platforms[(d - 1) % 2] if dtype_idx < 2 else platforms[2]
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
        if d <= 30:  # racked: 5 per rack, distinct u positions, front face
            rack_idx = (d - 1) // 5
            payload["site"] = site_ids[rack_idx // 2]
            payload["rack"] = rack_ids[rack_idx]
            payload["position"] = 5 * ((d - 1) % 5) + 1  # 5U spacing fits the 2.5U types
            payload["face"] = "front"
        else:  # unracked: site-only location path
            payload["site"] = site_ids[(d - 31) % 3]
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
        for i in range(2)
    ]
    devices = nb.dcim.devices.create(device_payloads)
    named = [d for d in devices if d.name]
    unnamed = [d for d in devices if not d.name]
    counts["dcim/devices"] = len(devices)

    # --- interfaces -----------------------------------------------------------
    iface_payloads = []
    interface_modes = ("access", "tagged", "tagged-all")
    for device_index, d in enumerate(named):
        for n in range(6):
            payload = {
                "device": d.id,
                "name": f"eth{n}",
                "type": "1000base-t",
                "description": f"phys {n}",
                "mtu": 1500,
            }
            if n < 3:
                payload["mode"] = interface_modes[n]
                if n == 0:
                    payload["untagged_vlan"] = grouped_vlans[device_index % 12].id
                elif n == 1:
                    payload["tagged_vlans"] = [
                        grouped_vlans[(device_index + 1) % 12].id,
                        grouped_vlans[(device_index + 2) % 12].id,
                    ]
            iface_payloads.append(payload)
        lag_mode = interface_modes[device_index % 3]
        lag_payload = {
            "device": d.id,
            "name": "Po1",
            "type": "lag",
            "description": "lag",
            "mode": lag_mode,
        }
        if lag_mode == "access":
            lag_payload["untagged_vlan"] = grouped_vlans[device_index % 12].id
        elif lag_mode == "tagged":
            lag_payload["tagged_vlans"] = [grouped_vlans[(device_index + 3) % 12].id]
        iface_payloads.append(lag_payload)
        uv = grouped_vlans[d.id % 12]
        tv = [grouped_vlans[(d.id + 1) % 12].id, grouped_vlans[(d.id + 2) % 12].id]
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
    interfaces = nb.dcim.interfaces.create(iface_payloads)
    counts["dcim/interfaces"] = len(interfaces)

    by_dev_name = {}
    for i in interfaces:
        by_dev_name[i.device.id, i.name] = i
    # LAG membership: eth4/eth5 join Po1
    for d in named:
        po1 = by_dev_name[d.id, "Po1"]
        for n in (4, 5):
            eth = by_dev_name[d.id, f"eth{n}"]
            eth.lag = po1.id
            eth.save()

    # --- IPAM: RIRs, aggregates, RTs, VRFs, prefixes, IPs ---------------------
    rirs = nb.ipam.rirs.create(
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
        ]
    )
    counts["ipam/rirs"] = len(rirs)

    aggregates = nb.ipam.aggregates.create(
        [
            {"prefix": "10.0.0.0/8", "rir": rirs[0].id, "date_added": "2026-01-01", "description": "seed"},
            {"prefix": "172.16.0.0/12", "rir": rirs[0].id, "date_added": "2026-01-01", "description": "seed"},
            {"prefix": "192.168.0.0/16", "rir": rirs[0].id, "date_added": "2026-01-01", "description": "seed"},
            {"prefix": "100.64.0.0/10", "rir": rirs[1].id, "date_added": "2026-01-01", "description": "seed"},
        ]
    )
    counts["ipam/aggregates"] = len(aggregates)

    rts = nb.ipam.route_targets.create([{"name": f"65000:{i}", "description": f"seed rt {i}"} for i in range(1, 5)])
    counts["ipam/route-targets"] = len(rts)

    vrfs = nb.ipam.vrfs.create(
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
        ]
    )
    counts["ipam/vrfs"] = len(vrfs)

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
    prefixes = nb.ipam.prefixes.create(prefix_payloads)
    counts["ipam/prefixes"] = len(prefixes)

    ip_payloads = []
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
    ips = nb.ipam.ip_addresses.create(ip_payloads)
    counts["ipam/ip-addresses"] = len(ips)

    # Deliberately leave every device without a primary IP: an ordinary null
    # cardinality-one relationship the saved-plan test relies on being absent.

    # --- circuits ---------------------------------------------------------------
    ctype = nb.circuits.circuit_types.create(name="transit", slug="transit")
    providers = nb.circuits.providers.create(
        [
            {"name": "provider-x", "slug": "provider-x", "description": "seed", "tags": tag_pair(4)},
            {"name": "provider-y", "slug": "provider-y", "description": "seed", "tags": tag_pair(5)},
        ]
    )
    counts["circuits/providers"] = len(providers)
    circuits = nb.circuits.circuits.create(
        [
            {
                "cid": f"CIR-{i:03d}",
                "provider": providers[i % 2].id,
                "type": ctype.id,
                "status": "active",
                "commit_rate": 1000 * (i + 1),
                "description": "seed",
            }
            for i in range(4)
        ]
    )
    counts["circuits/circuits"] = len(circuits)

    print(json.dumps(counts, indent=2))


if __name__ == "__main__":
    main()
