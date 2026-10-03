"""Measure the cost of requesting DiffSync identifiers during an Infrahub bulk load.

The v3 loader asks the SDK for the ordered union of a model's identifiers and mapped
attributes (``identifier_and_attribute_fields``). This script loads the same live
dataset twice through the real ``InfrahubAdapter.model_loader``:

* ``union``: the shipped behaviour.
* ``attrs``: a **measurement baseline only**, which patches the field selection down to
  ``model._attributes``. It is not a shippable strict-contract implementation.

Every GraphQL request the SDK sends is recorded (query text, response bytes, latency),
so request counts, bounded peer GETs, and response sizes come from the wire rather than
from the adapter. The direct ``list_existing_ids`` scan is measured separately because
no product entry point calls it.

Everything lives on one branch the run creates and deletes; ``main`` is never written.

    INFRAHUB_ADDRESS=http://127.0.0.1:8000 INFRAHUB_API_TOKEN=<token> \\
    uv run python development/identifier_prefetch_study.py --scale 1 --repeats 5 --output out.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
import time
import tracemalloc
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

import infrahub_sdk
import requests
from diffsync import Adapter
from infrahub_sdk import Config, InfrahubClientSync
from infrahub_sdk.exceptions import GraphQLError

from infrahub_sync import SchemaMappingField, SchemaMappingModel, SyncAdapter, SyncConfig
from infrahub_sync.adapters import infrahub as adapter_module
from infrahub_sync.adapters.infrahub import InfrahubAdapter, InfrahubModel

if TYPE_CHECKING:
    from collections.abc import Iterator

NS = "Bench"
GENERIC_HOST = "BenchHosting"

# kind -> (identifier fields, attribute fields, count at scale 1). Relationship identifiers are
# named after the relationship. 18 populated kinds, 420 objects.
SCALAR_KINDS = {
    "Site": 5,
    "Vendor": 4,
    "Platform": 4,
    "Role": 4,
    "Vlan": 12,
    "Tag": 5,
    "Prefix": 20,
}
# kind -> (peer kind, count). Identity is (<rel>, name).
REL_KINDS = {
    "Device": ("Site", 20),
    "Rack": ("Site", 10),
    "Pdu": ("Site", 6),
    "Circuit": ("Site", 10),
    "Interface": ("Device", 120),
    "InterfaceLag": ("Device", 20),
    "Module": ("Device", 40),
    "Psu": ("Device", 20),
    "Fan": ("Device", 20),
    "Port": ("Device", 80),
    "Mount": ("Rack", 20),  # peer kind is the generic in the "generic" variant below
}
ATTRS = ("description", "serial")


def _kind(short: str) -> str:
    return f"{NS}{short}"


def build_schema() -> dict[str, Any]:
    attr = [{"name": "name", "kind": "Text", "unique": True}]
    extra = [{"name": a, "kind": "Text", "optional": True} for a in ATTRS]
    nodes: list[dict[str, Any]] = []
    nodes.extend(
        {
            "name": short,
            "namespace": NS,
            "include_in_menu": False,
            "human_friendly_id": ["name__value"],
            "attributes": [{"name": "name", "kind": "Text", "unique": True}, *extra],
        }
        for short in SCALAR_KINDS
    )
    for short, (peer, _) in REL_KINDS.items():
        node: dict[str, Any] = {
            "name": short,
            "namespace": NS,
            "include_in_menu": False,
            "attributes": [*attr, *extra],
        }
        if short in {"Rack", "Pdu"}:
            node["inherit_from"] = [_kind("Hosting")]
        elif short == "Mount":
            # Generic relationship identity: the peer is any kind inheriting `Hosting`.
            node["human_friendly_id"] = ["host__name__value", "name__value"]
            node["relationships"] = [
                {"name": "host", "peer": _kind("Hosting"), "cardinality": "one", "optional": False, "kind": "Attribute"}
            ]
        else:
            node["human_friendly_id"] = [f"{peer.lower()}__name__value", "name__value"]
            node["relationships"] = [
                {
                    "name": peer.lower(),
                    "peer": _kind(peer),
                    "cardinality": "one",
                    "optional": False,
                    "kind": "Attribute",
                }
            ]
        nodes.append(node)
    return {
        "version": "1.0",
        "generics": [
            {
                "name": "Hosting",
                "namespace": NS,
                "include_in_menu": False,
                "human_friendly_id": ["site__name__value", "name__value"],
                "attributes": [*attr, *extra],
                "relationships": [
                    {
                        "name": "site",
                        "peer": _kind("Site"),
                        "cardinality": "one",
                        "optional": False,
                        "kind": "Attribute",
                    }
                ],
            }
        ],
        "nodes": nodes,
    }


def _rel_name(short: str) -> str:
    peer = REL_KINDS[short][0]
    return "host" if short == "Mount" else peer.lower()


def make_models() -> dict[str, type[InfrahubModel]]:
    models: dict[str, type[InfrahubModel]] = {}
    for short in SCALAR_KINDS:
        models[_kind(short)] = type(
            _kind(short),
            (InfrahubModel,),
            {
                "_modelname": _kind(short),
                "_identifiers": ("name",),
                "_attributes": ATTRS,
                "__annotations__": {"name": str, "description": str | None, "serial": str | None},
                "description": None,
                "serial": None,
            },
        )
    for short in REL_KINDS:
        rel = _rel_name(short)
        models[_kind(short)] = type(
            _kind(short),
            (InfrahubModel,),
            {
                "_modelname": _kind(short),
                "_identifiers": (rel, "name"),
                "_attributes": ATTRS,
                "__annotations__": {rel: str, "name": str, "description": str | None, "serial": str | None},
                "description": None,
                "serial": None,
            },
        )
    return models


def load_order() -> list[str]:
    scalar = list(SCALAR_KINDS)
    tiers = [
        ["Device", "Rack", "Pdu", "Circuit"],
        ["Interface", "InterfaceLag", "Module", "Psu", "Fan", "Port"],
        ["Mount"],
    ]
    return [_kind(k) for k in scalar + [k for tier in tiers for k in tier]]


def sync_config(order: list[str]) -> SyncConfig:
    return SyncConfig(
        name="study",
        source=SyncAdapter(name="netbox", adapter="x:x"),
        destination=SyncAdapter(name="infrahub", adapter="x:x"),
        order=order,
        schema_mapping=[
            SchemaMappingModel(
                name=kind,
                mapping=kind,
                identifiers=["name"],
                fields=[
                    SchemaMappingField(name=f, mapping=f)
                    for f in ("name", "description", "serial", "site", "device", "rack", "host")
                ],
            )
            for kind in order
        ],
    )


class Recorder:
    """Wrap the SDK's ``_post`` to record every GraphQL request."""

    def __init__(self, client: InfrahubClientSync) -> None:
        self.rows: list[dict[str, Any]] = []
        original = client._post

        def post(url: str, payload: dict, *args: Any, **kwargs: Any) -> Any:
            start = time.perf_counter()
            response = original(url, payload, *args, **kwargs)
            query = payload.get("query", "") if isinstance(payload, dict) else ""
            self.rows.append(
                {
                    "bytes": len(response.content),
                    "seconds": time.perf_counter() - start,
                    "query_chars": len(query),
                    "query_fields": len(re.findall(r"[A-Za-z_]+", query)),
                    "kind": "peer_get" if re.search(r"\bids:\s*\[", query) else "bulk",
                    "errors": "errors" in response.text[:200],
                    "query": query,
                }
            )
            return response

        client._post = post

    def summary(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for label in ("bulk", "peer_get"):
            rows = [r for r in self.rows if r["kind"] == label]
            out[label] = {
                "requests": len(rows),
                "response_bytes": sum(r["bytes"] for r in rows),
                "request_seconds": round(sum(r["seconds"] for r in rows), 4),
                "max_query_chars": max((r["query_chars"] for r in rows), default=0),
                "server_errors": sum(1 for r in rows if r["errors"]),
            }
        out["total_requests"] = len(self.rows)
        out["total_bytes"] = sum(r["bytes"] for r in self.rows)
        return out


class Harness(InfrahubAdapter):
    """A real adapter on a live client, without the constructor's account lookups."""

    def __init__(self, *, config: SyncConfig, client: InfrahubClientSync, schema: Any, models: dict) -> None:
        Adapter.__init__(self)
        self.target = "destination"
        self.config = config
        self.client = client
        self.schema = schema
        self.source_node = None
        self.owner_node = None
        self.continue_on_error = False
        self._peer_unique_ids = {}
        for name, model in models.items():
            setattr(self, name, model)


@contextmanager
def selection(mode: str) -> Iterator[None]:
    original = adapter_module.identifier_and_attribute_fields
    if mode == "attrs":
        adapter_module.identifier_and_attribute_fields = lambda model: list(model._attributes)  # ty: ignore[invalid-assignment]
    try:
        yield
    finally:
        adapter_module.identifier_and_attribute_fields = original


def new_client(address: str, token: str, branch: str, page: int) -> InfrahubClientSync:
    return InfrahubClientSync(
        address=address,
        config=Config(address=address, api_token=token, default_branch=branch, pagination_size=page, timeout=120),
    )


def run_load(
    address: str,
    token: str,
    branch: str,
    mode: str,
    page: int,
    models: dict,
    schema_dict: Any,
    *,
    trace: bool = False,
    reverse: bool = False,
) -> dict[str, Any]:
    client = new_client(address, token, branch, page)
    recorder = Recorder(client)
    order = load_order()
    if reverse:
        # Consumers before their peers: the bulk loader's worst case for the peer fallback.
        order = list(reversed(order))
    harness = Harness(config=sync_config(order), client=client, schema=schema_dict, models=models)
    if trace:
        tracemalloc.start()
    start = time.perf_counter()
    with selection(mode):
        for kind in order:
            harness.model_loader(model_name=kind, model=models[kind])
    seconds = time.perf_counter() - start
    peak = 0
    if trace:
        peak = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()
    loaded = sum(len(list(harness.get_all(models[k]))) for k in order)
    result = {
        "mode": mode,
        "reverse_order": reverse,
        "seconds": round(seconds, 3),
        "objects_loaded": loaded,
        **recorder.summary(),
    }
    if trace:
        result["peak_traced_mib"] = round(peak / 2**20, 1)
    result["_sample_queries"] = {
        k: next((r["query"] for r in recorder.rows if r["kind"] == "bulk" and k in r["query"]), "") for k in order[-6:]
    }
    return result


def run_id_scan(
    address: str, token: str, branch: str, mode: str, page: int, models: dict, schema_dict: Any
) -> dict[str, Any]:
    client = new_client(address, token, branch, page)
    recorder = Recorder(client)
    order = load_order()
    harness = Harness(config=sync_config(order), client=client, schema=schema_dict, models=models)
    start = time.perf_counter()
    count = 0
    with selection(mode):
        for kind in order:
            count += sum(1 for _ in harness.list_existing_ids(kind))
    return {"mode": mode, "seconds": round(time.perf_counter() - start, 3), "ids": count, **recorder.summary()}


def populate(client: InfrahubClientSync, branch: str, scale: int) -> dict[str, int]:
    ids: dict[str, list[str]] = {}
    counts: dict[str, int] = {}

    def create_many(short: str, rows: list[dict[str, Any]]) -> None:
        batch = client.create_batch()
        nodes = []
        for row in rows:
            node = client.create(kind=_kind(short), branch=branch, data=row)
            nodes.append(node)
            batch.add(task=node.save, node=node)
        for _ in batch.execute():
            pass
        ids[short] = [n.id for n in nodes]
        counts[short] = len(nodes)

    for short, n in SCALAR_KINDS.items():
        create_many(
            short,
            [{"name": f"{short.lower()}-{i}", "description": f"d{i}", "serial": f"s{i}"} for i in range(n * scale)],
        )
    for tier in (
        ["Device", "Rack", "Pdu", "Circuit"],
        ["Interface", "InterfaceLag", "Module", "Psu", "Fan", "Port"],
        ["Mount"],
    ):
        for short in tier:
            peer, n = REL_KINDS[short]
            n *= scale
            rel = _rel_name(short)
            # A peer of kind Hosting is a Rack for the generic variant.
            peers = ids[peer]
            create_many(
                short,
                [
                    {
                        "name": f"{short.lower()}-{i}",
                        rel: peers[i % len(peers)],
                        "description": f"d{i}",
                        "serial": f"s{i}",
                    }
                    for i in range(n)
                ],
            )
    return counts


def median(values: list[float]) -> float:
    return round(statistics.median(values), 3)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scale", type=int, default=1)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--page", type=int, default=50)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    address = os.environ["INFRAHUB_ADDRESS"]
    token = os.environ["INFRAHUB_API_TOKEN"]
    branch = f"prefetch-study-{uuid.uuid4().hex[:8]}"
    admin = new_client(address, token, "main", args.page)
    admin.branch.create(branch_name=branch, sync_with_git=False)
    report: dict[str, Any] = {"scale": args.scale, "page": args.page, "repeats": args.repeats}
    try:
        client = new_client(address, token, branch, args.page)
        response = requests.post(
            f"{address}/api/schema/load?branch={branch}",
            headers={"X-INFRAHUB-KEY": token},
            json={"schemas": [build_schema()]},
            timeout=120,
            allow_redirects=False,
        )
        response.raise_for_status()
        client.schema.wait_until_converged(branch=branch)
        for _ in range(60):
            if _kind("Mount") in client.schema.all(branch=branch, refresh=True):
                break
            time.sleep(1)
        info = requests.get(f"{address}/api/info", headers={"X-INFRAHUB-KEY": token}, timeout=30).json()
        report["server_version"] = info.get("version")
        report["sdk_version"] = infrahub_sdk.__version__
        t0 = time.perf_counter()
        report["populated"] = populate(client, branch, args.scale)
        report["objects"] = sum(report["populated"].values())
        report["populate_seconds"] = round(time.perf_counter() - t0, 1)
        schema_dict = client.schema.all(branch=branch)
        models = make_models()

        # Warm-up so the server's own caches are in the same state for both variants.
        run_load(address, token, branch, "union", args.page, models, schema_dict)
        runs: dict[str, list[dict[str, Any]]] = {"union": [], "attrs": []}
        for i in range(args.repeats):
            order = ("union", "attrs") if i % 2 == 0 else ("attrs", "union")
            for mode in order:
                runs[mode].append(run_load(address, token, branch, mode, args.page, models, schema_dict))
        for mode in runs:
            runs[mode][-1]["_with_memory"] = run_load(
                address, token, branch, mode, args.page, models, schema_dict, trace=True
            )
        report["runs"] = runs
        report["reverse_order"] = {
            mode: [
                run_load(address, token, branch, mode, args.page, models, schema_dict, reverse=True) for _ in range(3)
            ]
            for mode in ("union", "attrs")
        }
        report["id_scan"] = {
            mode: [run_id_scan(address, token, branch, mode, args.page, models, schema_dict) for _ in range(3)]
            for mode in ("union", "attrs")
        }
        report["medians"] = {
            mode: {
                "seconds": median([r["seconds"] for r in rs]),
                "total_requests": rs[0]["total_requests"],
                "bulk_requests": rs[0]["bulk"]["requests"],
                "peer_get_requests": rs[0]["peer_get"]["requests"],
                "total_bytes": rs[0]["total_bytes"],
                "bulk_bytes": rs[0]["bulk"]["response_bytes"],
                "peer_get_bytes": rs[0]["peer_get"]["response_bytes"],
                "max_query_chars": rs[0]["bulk"]["max_query_chars"],
                "server_errors": sum(r["bulk"]["server_errors"] + r["peer_get"]["server_errors"] for r in rs),
                "peak_traced_mib": rs[-1]["_with_memory"].get("peak_traced_mib"),
            }
            for mode, rs in runs.items()
        }
        with Path(args.output).open("w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2, default=str)
    finally:
        try:
            admin.branch.delete(branch_name=branch)
        except GraphQLError as exc:
            # Neo4j's per-transaction memory cap can refuse a large branch delete; the study's
            # disposable stack is removed with its volumes, so report it rather than hide it.
            print(f"branch {branch} was not deleted: {exc.errors[0].get('message')}", file=sys.stderr)
    json.dump(report["medians"], sys.stdout, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
