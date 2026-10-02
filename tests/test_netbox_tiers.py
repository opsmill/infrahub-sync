"""Pure tier contracts and mocked REST writes, without live NetBox or Infrahub."""

from __future__ import annotations

import hashlib
import json
import math
import subprocess  # noqa: S404 -- cold-import probe runs only this Python interpreter
import sys
from collections import Counter
from contextlib import nullcontext
from typing import TYPE_CHECKING, Any

import httpx
import pytest

from development.netbox.datasets import change_netbox as change
from development.netbox.datasets import seed_netbox as seed
from development.netbox.datasets.netbox_api import BATCH_SIZE, NetboxAPI, relation_kind, seed_dataset, verify_counts
from development.netbox.datasets.tier_data import FOUNDATION_COUNTS, KINDS, SKIP_COUNTS, TIER_COUNTS, build_dataset

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    ("tier", "total", "skips"), [("S", 560, (2, 4, 2)), ("M", 10207, (8, 16, 12)), ("L", 87815, (35, 70, 40))]
)
def test_tier_count_table_and_skip_cases(tier: str, total: int, skips: tuple[int, int, int]) -> None:
    """Match all mapped counts and deliberate skip cases at each tier."""
    data = build_dataset(tier)
    assert len(TIER_COUNTS[tier]) == len(KINDS)
    assert sum(TIER_COUNTS[tier].values()) == total
    assert tuple(SKIP_COUNTS[tier].values()) == skips
    for kind, count in TIER_COUNTS[tier].items():
        assert len(data[kind]) == count + SKIP_COUNTS[tier].get(kind, 0)
        assert len(change.eligible(kind, data)) == count


def test_s_retains_every_original_seed_payload_and_lag_relationship() -> None:
    # Captured from the pre-tier seeder at 70c31576 using endpoint-local sequential IDs.
    # This covers every field, name, skip case and relationship, rather than counts alone.
    """Preserve every original S payload and relationship by its recorded digest."""
    data = {kind: [row.fields for row in rows] for kind, rows in build_dataset("S").items()}
    encoded = json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    assert hashlib.sha256(encoded).hexdigest() == "ec37cd8332faab01335a5386945e76759879cdf78da69667d51a7a06b0a45094"


@pytest.mark.parametrize(("tier", "per_rack"), [("M", 20), ("L", 35)])
def test_scaled_shape_has_collision_free_addresses_matching_manufacturers_and_safe_racks(
    tier: str, per_rack: int
) -> None:
    """Verify scaled addresses, rack occupancy, manufacturer matching and relationships."""
    data = build_dataset(tier)
    devices = change.eligible("dcim/devices", data)
    racked = [row for row in devices if "rack" in row.fields]
    assert len(racked) * 4 == len(devices) * 3
    assert set(Counter(row.fields["rack"] for row in racked).values()) == {per_rack}
    positions = [(row.fields["rack"], row.fields["position"]) for row in racked]
    assert len(positions) == len(set(positions))
    for row in devices:
        dtype = data["dcim/device-types"][row.fields["device_type"] - 1]
        platform = data["dcim/platforms"][row.fields["platform"] - 1]
        assert dtype.fields["manufacturer"] == platform.fields["manufacturer"]
        if "rack" in row.fields:
            assert dtype.fields["u_height"] == 1
            assert row.fields["position"] <= per_rack
    assert sum(row.fields["u_height"] % 1 != 0 for row in data["dcim/device-types"]) >= 2
    addresses = [row.fields["address"] for row in data["ipam/ip-addresses"]]
    assert len(addresses) == len(set(addresses))
    assert addresses[:3] == [f"10.0.0.{host}/24" for host in (1, 2, 3)]
    assert all(row.fields["vrf"] for row in data["ipam/ip-addresses"])
    assert Counter(row.fields["type"] for row in change.eligible("dcim/interfaces", data)) == {
        "1000base-t": len(devices) * 6,
        "lag": len(devices),
        "virtual": len(devices),
    }
    assert sum("lag" in row.fields for row in data["dcim/interfaces"]) == len(devices) * 2
    assert sum(row.fields["status"] == "container" for row in data["ipam/prefixes"]) == round(
        TIER_COUNTS[tier]["ipam/prefixes"] * 0.07
    )
    for kind, rows in data.items():
        if kind in KINDS and kind != "extras/tags":
            assert all(len(row.fields["tags"]) == len(set(row.fields["tags"])) == 2 for row in rows)


@pytest.mark.parametrize(
    ("tier", "totals"),
    [
        ("S", {"update": 4, "create": 1, "delete": 1}),
        ("M", {"update": 72, "create": 20, "delete": 10}),
        ("L", {"update": 615, "create": 175, "delete": 88}),
    ],
)
def test_change_plan_is_deterministic_proportional_and_has_only_unreferenced_leaf_deletes(
    tier: str, totals: dict[str, int]
) -> None:
    """Verify deterministic plans, proportional actions and safe leaf deletions."""
    first = change.plan_changes(tier)
    assert change.expected_text(tier, first) == change.expected_text(tier, change.plan_changes(tier))
    expected = json.loads(change.expected_text(tier, first))
    assert expected["format_version"] == 1
    assert expected["tier"] == tier
    assert expected["counts"] == totals
    assert Counter(row["action"] for row in first) == totals
    data = build_dataset(tier)
    updates = [row for row in first if row["action"] == "update"]
    assert Counter(row["kind"] for row in updates) == +Counter(change.apportion(totals["update"], TIER_COUNTS[tier]))
    assert sum("untagged_vlan" in row["fields"] for row in updates) >= math.ceil(totals["update"] * 0.3)
    keys = [(row["kind"], row["identifier"]) for row in first]
    assert len(keys) == len(set(keys))
    baselines = {
        kind: {change.identifier(kind, item.fields, data) for item in change.eligible(kind, data)} for kind in KINDS
    }
    leaves = {
        kind: {change.identifier(kind, item.fields, data) for item in change.unreferenced(kind, data)}
        for kind in change.LEAF_KINDS
    }
    for row in first:
        kind, ident = row["kind"], row["identifier"]
        baseline = baselines[kind]
        if row["action"] in {"create", "delete"}:
            assert kind in change.LEAF_KINDS
        if row["action"] == "delete":
            assert ident in leaves[kind]
        elif row["action"] == "create":
            assert ident not in baseline
        else:
            assert ident in baseline
        assert {"action", "kind", "identifier", "fields"} == row.keys()


class MemoryNetbox(NetboxAPI):
    """A REST substitute that deliberately assigns IDs unrelated to seed ordinals."""

    def __init__(self) -> None:
        """Start with empty endpoint records and a write-call log."""
        self.rows: dict[str, list[dict[str, Any]]] = {}
        self.calls: list[tuple[str, str, Any, str]] = []

    def all(self, kind: str) -> list[dict[str, Any]]:
        """Return the current in-memory records for an endpoint."""
        return self.rows.get(kind, [])

    def count(self, kind: str) -> int:
        """Return the in-memory endpoint total."""
        return len(self.all(kind))

    def request(self, method: str, kind: str, payload: Any = None, suffix: str = "") -> Any:  # noqa: ANN401 -- mirrors JSON client boundary
        """Emulate writes with nonordinal IDs and nested relationship records."""
        self.calls.append((method, kind, payload, suffix))
        rows = self.rows.setdefault(kind, [])
        if method == "POST":
            if not isinstance(payload, list):
                return self.request(method, kind, [payload])[0]
            result = []
            for fields in payload:
                record: dict[str, Any] = dict(fields, id=1000 + len(rows) * 7)
                for field, value in fields.items():
                    target = relation_kind(kind, field)
                    if target and value is not None:
                        candidates = {r["id"]: r for r in self.all(target)}
                        record[field] = [candidates[i] for i in value] if isinstance(value, list) else candidates[value]
                rows.append(record)
                result.append(record)
            return result
        if isinstance(payload, list):
            for fields in payload:
                row = next(row for row in rows if row["id"] == fields["id"])
                row.update(fields)
            return payload
        row = next(row for row in rows if row["id"] == int(suffix.rstrip("/")))
        if method == "DELETE":
            rows.remove(row)
        elif method == "PATCH":
            row.update(payload)
        return row


def test_seed_writer_resolves_real_ids_batches_creates_and_full_lag_updates(monkeypatch: pytest.MonkeyPatch) -> None:
    """Resolve nonordinal IDs and batch both creates and LAG membership updates."""
    api = MemoryNetbox()
    # Exercise multiple batches at S without building a second live-sized tier.
    monkeypatch.setattr("development.netbox.datasets.netbox_api.BATCH_SIZE", 17)
    seed_dataset(api, build_dataset("S"), "S")
    posts = [payload for method, _kind, payload, _suffix in api.calls if method == "POST"]
    assert max(map(len, posts)) <= 17
    patches = [
        payload for method, kind, payload, _suffix in api.calls if method == "PATCH" and kind == "dcim/interfaces"
    ]
    assert sum(map(len, patches)) == 80
    assert all(payload["lag"] >= 1000 for batch in patches for payload in batch)
    assert api.all("dcim/devices")[0]["device_type"]["id"] == 1000
    assert api.all("ipam/ip-addresses")[0]["assigned_object_id"]["name"] == "eth0"


def test_change_writer_payloads_marker_and_expected_file(tmp_path: Path) -> None:
    """Resolve mutations, mark starts and publish the expected file on success."""
    api = MemoryNetbox()
    seed_dataset(api, build_dataset("S"), "S")
    api.calls.clear()
    output = tmp_path / "S.expected.json"
    change.apply_changes(api, "S", output)
    assert output.read_text() == change.expected_text("S", change.plan_changes("S"))
    calls = api.calls
    assert calls[0][0:2] == ("PATCH", "extras/tags")
    assert calls[0][2] == {"slug": change.MARKER_SLUG}
    assert len(api.all("extras/tags")) == TIER_COUNTS["S"]["extras/tags"]
    assert Counter(method for method, _kind, _payload, _suffix in calls[1:]) == {"PATCH": 4, "POST": 2, "DELETE": 1}
    vlans = [
        payload["untagged_vlan"]
        for method, kind, payload, _suffix in calls
        if method == "PATCH" and kind == "dcim/interfaces" and "untagged_vlan" in payload
    ]
    assert vlans
    assert all(vlan >= 1000 for vlan in vlans)
    api.calls.clear()
    with pytest.raises(ValueError, match="changes already started"):
        change.apply_changes(api, "S", output)
    assert api.calls == []


def test_change_refuses_wrong_tier_before_writing(tmp_path: Path) -> None:
    """Reject a mismatched restored tier before any mutation."""
    api = MemoryNetbox()
    seed_dataset(api, build_dataset("S"), "S")
    api.calls.clear()
    with pytest.raises(ValueError, match="restore tier M"):
        change.apply_changes(api, "M", tmp_path / "M.expected.json")
    assert api.calls == []


@pytest.mark.parametrize(("tier", "restored"), [("S", "M"), ("S", "L"), ("M", "L"), ("M", "S"), ("L", "S"), ("L", "M")])
@pytest.mark.parametrize("force", [False, True])
def test_change_refuses_other_tier_counts_before_writes_or_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, tier: str, restored: str, *, force: bool
) -> None:
    """Reject every mismatched restored tier even when forced, before publishing files."""
    api = MemoryNetbox()
    counts = TIER_COUNTS[restored] | FOUNDATION_COUNTS
    monkeypatch.setattr(api, "count", lambda kind: counts[kind] + SKIP_COUNTS[restored].get(kind, 0))
    output = tmp_path / f"{tier}.expected.json"
    output.write_text("previous result")
    with pytest.raises(ValueError, match=f"restore tier {tier}"):
        change.apply_changes(api, tier, output, force=force)
    assert api.calls == []
    assert output.read_text() == "previous result"
    assert not output.with_suffix(".partial.json").exists()


@pytest.mark.parametrize("kind", list(FOUNDATION_COUNTS))
@pytest.mark.parametrize("count", [0, 2])
def test_verify_only_refuses_missing_or_extra_foundations(
    monkeypatch: pytest.MonkeyPatch, kind: str, count: int
) -> None:
    """The pre-dump command rejects missing or extra roles and circuit types."""
    api = MemoryNetbox()
    seed_dataset(api, build_dataset("S"), "S")
    api.rows[kind] = api.rows[kind][:count] if count == 0 else api.rows[kind] * count
    api.calls.clear()
    monkeypatch.setattr(seed, "environment_credentials", lambda: ("http://example.invalid", "unused"))
    monkeypatch.setattr(seed, "NetboxAPI", lambda _url, _token: nullcontext(api))
    monkeypatch.setattr(sys, "argv", ["seed_netbox.py", "--tier", "S", "--verify-only"])
    with pytest.raises(RuntimeError, match=f"wrong final count for {kind}"):
        verify_counts(api, "S")
    with pytest.raises(SystemExit) as exit_error:
        seed.main()
    assert exit_error.value.code == 1
    assert api.calls == []


def test_force_retries_after_partial_count_changes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Allow the selected tier's forced retry after a completed deletion and failed update."""
    api = MemoryNetbox()
    seed_dataset(api, build_dataset("S"), "S")
    original = api.request

    def fail_update(method: str, kind: str, payload: Any = None, suffix: str = "") -> Any:  # noqa: ANN401 -- mirrors JSON client boundary
        """Stop after the planned deletion without undoing its changed endpoint count."""
        if method == "PATCH" and kind != "extras/tags":
            msg = "update failed"
            raise RuntimeError(msg)
        return original(method, kind, payload, suffix)

    monkeypatch.setattr(api, "request", fail_update)
    output = tmp_path / "S.expected.json"
    with pytest.raises(RuntimeError, match="update failed"):
        change.apply_changes(api, "S", output)
    monkeypatch.setattr(api, "request", original)
    change.apply_changes(api, "S", output, force=True)
    assert output.read_text() == change.expected_text("S", change.plan_changes("S"))


def test_failed_mutation_leaves_marker_and_no_success_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep partial-run evidence and remove stale success output on failure."""
    api = MemoryNetbox()
    seed_dataset(api, build_dataset("S"), "S")
    original = api.request

    def fail_delete(method: str, kind: str, payload: Any = None, suffix: str = "") -> Any:  # noqa: ANN401 -- mirrors JSON client boundary
        """Inject a delete failure after earlier mutations have started."""
        if method == "DELETE":
            msg = "HTTP failure"
            raise RuntimeError(msg)
        return original(method, kind, payload, suffix)

    monkeypatch.setattr(api, "request", fail_delete)
    output = tmp_path / "S.expected.json"
    output.write_text("stale result")
    with pytest.raises(RuntimeError, match="HTTP failure"):
        change.apply_changes(api, "S", output)
    assert not output.exists()
    assert output.with_suffix(".partial.json").exists()
    assert any(row["slug"] == change.MARKER_SLUG for row in api.all("extras/tags"))


def test_http_failure_hides_credentials_and_server_body() -> None:
    """Report HTTP status without exposing credentials or response bodies."""

    def handler(request: httpx.Request) -> httpx.Response:
        """Return a synthetic forbidden response."""
        assert request.headers["Authorization"] == "Bearer synthetic-secret"
        return httpx.Response(403, json={"error": "synthetic-secret"})

    with NetboxAPI("http://example.invalid", "synthetic-secret") as api:
        api.client.close()
        api.client = httpx.Client(
            base_url="http://example.invalid",
            transport=httpx.MockTransport(handler),
            headers={"Authorization": "Bearer synthetic-secret"},
        )
        with pytest.raises(RuntimeError, match="HTTP 403") as error:
            api.request("GET", "dcim/sites")
        assert "synthetic-secret" not in str(error.value)
        assert error.value.__suppress_context__


def test_api_pagination_uses_bounded_offsets() -> None:
    """Read successive bounded pages using deterministic offsets."""
    offsets: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        """Return one synthetic bounded page and record its offset."""
        offsets.append(request.url.params["offset"])
        assert request.url.params["limit"] == str(BATCH_SIZE)
        return httpx.Response(
            200, json={"results": [{"id": len(offsets)}], "next": "next" if len(offsets) == 1 else None}
        )

    with NetboxAPI("http://example.invalid", "unused") as api:
        api.client.close()
        api.client = httpx.Client(base_url="http://example.invalid", transport=httpx.MockTransport(handler))
        assert api.all("dcim/sites") == [{"id": 1}, {"id": 2}]
    assert offsets == ["0", str(BATCH_SIZE)]


def test_force_reapplies_updates_without_duplicate_creates_or_deletes(tmp_path: Path) -> None:
    """Repeat updates while skipping completed creates and deletions."""
    api = MemoryNetbox()
    seed_dataset(api, build_dataset("S"), "S")
    output = tmp_path / "S.expected.json"
    change.apply_changes(api, "S", output)
    api.calls.clear()
    change.apply_changes(api, "S", output, force=True)
    assert Counter(method for method, _kind, _payload, _suffix in api.calls) == {"PATCH": 4}
    assert output.read_text() == change.expected_text("S", change.plan_changes("S"))


def test_force_finds_prefix_updates_after_namespace_identity_change() -> None:
    """Alias a prefix update after its VRF changes its natural key."""
    data = build_dataset("M")
    changes = change.plan_changes("M")
    row = next(c for c in changes if c["kind"] == "ipam/prefixes" and "vrf" in c["fields"])
    original = next(r for r in data[row["kind"]] if change.identifier(row["kind"], r.fields, data) == row["identifier"])
    target = row["fields"]["vrf"]
    vrf = next(r for r in data["ipam/vrfs"] if change.identifier("ipam/vrfs", r.fields, data) == target["identifier"])
    after = original.fields | {"vrf": vrf.id}
    ids = {(row["kind"], change.identifier(row["kind"], after, data)): 999}
    change.add_update_aliases([row], data, ids)
    assert ids[row["kind"], row["identifier"]] == 999


@pytest.mark.parametrize("module", ["change_netbox", "seed_netbox"])
def test_dataset_import_preserves_search_path_and_precedence(module: str) -> None:
    # A fresh process exercises the first import, before pytest collection caches it.
    """Keep ordinary imports from changing module search order."""
    probe = """
import importlib
import importlib.util
import sys
before = sys.path.copy()
origin = importlib.util.find_spec("tasks").origin
importlib.import_module(sys.argv[1])
assert sys.path == before
assert importlib.util.find_spec("tasks").origin == origin
"""
    result = subprocess.run(  # noqa: S603 -- fixed probe and parametrized local module names
        [sys.executable, "-c", probe, f"development.netbox.datasets.{module}"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_change_cli_defaults_to_repository_changes_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Write the default expected file under the repository state directory."""
    api = MemoryNetbox()
    seed_dataset(api, build_dataset("S"), "S")
    monkeypatch.setattr(change, "__file__", str(tmp_path / "development/netbox/datasets/change_netbox.py"))
    monkeypatch.setattr(sys, "argv", ["change_netbox.py", "--tier", "S"])
    monkeypatch.setenv("NETBOX_URL", "http://example.invalid")
    monkeypatch.setenv("NETBOX_API_TOKEN", "unused")

    def client(url: str, token: str) -> nullcontext[MemoryNetbox]:
        """Validate environment credentials and return the in-memory client."""
        assert url == "http://example.invalid"
        assert token == "unused"  # noqa: S105 -- synthetic credential for the mocked client
        return nullcontext(api)

    monkeypatch.setattr(change, "NetboxAPI", client)
    change.main()
    output = tmp_path / ".netbox/changes/S.expected.json"
    assert output.read_text() == change.expected_text("S", change.plan_changes("S"))
    assert not output.with_suffix(".partial.json").exists()


@pytest.mark.parametrize("kind", ["ipam/prefixes", "ipam/ip-addresses"])
def test_change_refuses_missing_vrf_before_writing(kind: str, tmp_path: Path) -> None:
    """Reject incomplete nested VRF records before mutation or output files."""
    api = MemoryNetbox()
    seed_dataset(api, build_dataset("S"), "S")
    api.all(kind)[0]["vrf"] = None
    api.calls.clear()
    output = tmp_path / "S.expected.json"
    with pytest.raises(ValueError, match=f"expected {kind} object has no VRF"):
        change.apply_changes(api, "S", output)
    assert api.calls == []
    assert not output.exists()
    assert not output.with_suffix(".partial.json").exists()
