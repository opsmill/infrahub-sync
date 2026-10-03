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

The model classes come from the product's own ``build_runtime_models`` over the live
schema, so ``_identifiers`` and ``_attributes`` are the runtime projection of the
configuration, not hand-built stand-ins. Two scenarios share that machinery:

* ``synthetic``: builds a schema and data set on one branch the run creates and deletes.
  A scale probe, not the NetBox scenario.
* ``netbox``: reads an existing destination branch that a registered NetBox import
  already populated, using that package's mapping. It writes nothing.

Neither scenario writes ``main``.

    INFRAHUB_ADDRESS=http://127.0.0.1:8000 INFRAHUB_API_TOKEN=<token> \\
    uv run python development/identifier_prefetch_study.py --scale 1 --repeats 5 --output out.json

    INFRAHUB_ADDRESS=http://127.0.0.1:8080 INFRAHUB_API_TOKEN=<token> \\
    uv run python development/identifier_prefetch_study.py --scenario netbox \\
        --package .netbox/from-netbox.local.yml --branch netbox-import --output out.json
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
import yaml
from diffsync import Adapter
from infrahub_sdk import Config, InfrahubClientSync
from infrahub_sdk.exceptions import GraphQLError, ServerNotResponsiveError

from infrahub_sync import SchemaMappingField, SchemaMappingModel, SyncAdapter, SyncConfig
from infrahub_sync.adapters import infrahub as adapter_module
from infrahub_sync.adapters.infrahub import InfrahubAdapter, InfrahubModel, identifier_and_attribute_fields
from infrahub_sync.configuration.capabilities import (
    _build_schema_snapshot,  # noqa: PLC2701  # the worker's own schema read
)
from infrahub_sync.runtime_schema import build_runtime_models
from infrahub_sync.runtime_schema.domain import normalize_destination_schema

if TYPE_CHECKING:
    from collections.abc import Iterator

NS = "Bench"

# kind -> count at scale 1. Identity is (name).
SCALAR_KINDS = {
    "Site": 5,
    "Vendor": 4,
    "Platform": 4,
    "Role": 4,
    "Vlan": 12,
    "Tag": 5,
    "Prefix": 20,
}
# kind -> (peer kind, count). Identity is (<rel>, name). 18 populated kinds, 420 objects.
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
    "Mount": ("Rack", 20),  # peer is the `Hosting` generic: see _rel_name and build_schema
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


def synthetic_config() -> SyncConfig:
    """The configuration a user would write for the synthetic schema.

    Identifiers match the schema's human-friendly ids: ``name`` for scalar kinds and
    ``(<relationship>, name)`` for the rest. Every kind maps its own identifier,
    relationship and attribute fields, and each relationship carries its peer kind as
    ``reference`` so the dependency order is computed the way a product run computes it.
    """
    mappings = [
        SchemaMappingModel(
            name=_kind(short),
            mapping=_kind(short),
            identifiers=["name"],
            fields=[SchemaMappingField(name=f, mapping=f) for f in ("name", *ATTRS)],
        )
        for short in SCALAR_KINDS
    ]
    for short, (peer, _) in REL_KINDS.items():
        rel = _rel_name(short)
        reference = _kind("Hosting") if short == "Mount" else _kind(peer)
        mappings.append(
            SchemaMappingModel(
                name=_kind(short),
                mapping=_kind(short),
                identifiers=[rel, "name"],
                fields=[
                    *(SchemaMappingField(name=f, mapping=f) for f in ("name", *ATTRS)),
                    SchemaMappingField(name=rel, mapping=rel, reference=reference),
                ],
            )
        )
    return SyncConfig(
        name="study",
        source=SyncAdapter(name="netbox", adapter="x:x"),
        destination=SyncAdapter(name="infrahub", adapter="x:x"),
        schema_mapping=mappings,
    )


def netbox_config(package: str) -> SyncConfig:
    """The mapping of a registered package, with its settings replaced by placeholders.

    The harness never reads adapter settings, so no URL or credential reference is kept.
    """
    document = yaml.safe_load(Path(package).read_text(encoding="utf-8"))
    body = document["configuration"]
    body["source"] = {"name": body["source"]["name"], "adapter": "x:x"}
    body["destination"] = {"name": body["destination"]["name"], "adapter": "x:x"}
    return SyncConfig(**body)


class Plan:
    """Runtime models, generic peers and the load orders for one configuration."""

    def __init__(self, config: SyncConfig, schema: Any) -> None:
        self.snapshot = normalize_destination_schema(_build_schema_snapshot(schema))
        self.config = config
        self.models = build_runtime_models(snapshot=self.snapshot, configuration=config, model_base=InfrahubModel)
        self.dependency_order, _ = config.compute_order_and_tiers(self.snapshot.generic_peers)
        self.dependency_order = [k for k in self.dependency_order if k in self.models]

    def explicit_order(self, order: list[str]) -> list[str]:
        """Return the order the product uses when a configuration lists ``order`` itself."""
        explicit = config_with_order(self.config, order)
        resolved, tiers = explicit.compute_order_and_tiers(self.snapshot.generic_peers)
        if tiers is not None or resolved != order:
            msg = "an explicit order was not returned unchanged by compute_order_and_tiers"
            raise RuntimeError(msg)
        return resolved

    def projection(self) -> dict[str, dict[str, Any]]:
        """What each model asks the SDK for, from the runtime models (not hand-built)."""
        return {
            kind: {
                "identifiers": list(model._identifiers),
                "attributes": list(model._attributes),
                "selection": identifier_and_attribute_fields(model),
            }
            for kind, model in sorted(self.models.items())
        }


def config_with_order(config: SyncConfig, order: list[str]) -> SyncConfig:
    return config.model_copy(update={"order": list(order)})


_TOKEN = re.compile(r'"(?:\\.|[^"\\])*"|\.\.\.|@\w+|[A-Za-z_]\w*|[{}():]')


def graphql_selections(query: str) -> list[str]:
    """Return the dotted path of every field selected in a GraphQL document.

    A field is one name in a selection set. The operation keyword and name, arguments,
    aliases, directives and the type condition of an inline fragment are not fields.
    An inline fragment contributes a ``...on Type`` path segment but is not counted.
    """
    tokens = _TOKEN.findall(query)
    paths: list[str] = []
    stack: list[str] = []
    started = False
    parens = 0
    last = ""
    previous = ""
    skip_type = False
    for index, tok in enumerate(tokens):
        if tok == "(":
            parens += 1
        elif tok == ")":
            parens -= 1
        elif parens or tok.startswith(('"', "@")):
            pass
        elif tok == "{":
            stack.append(last)
            started = True
            last = ""
        elif tok == "}":
            stack.pop()
        elif not started or tok in {":", "..."}:
            pass
        elif previous == "...":
            # `... on Type`: the keyword `on` is followed by the type condition.
            skip_type = True
        elif skip_type:
            last = f"...on {tok}"
            skip_type = False
        elif index + 1 < len(tokens) and tokens[index + 1] == ":":
            pass  # an alias; the field name follows the colon
        else:
            last = tok
            paths.append(".".join([*(s for s in stack[1:] if s), tok]))
        previous = tok
    return paths


def root_field(query: str) -> str:
    selections = graphql_selections(query)
    return selections[0].split(".")[0] if selections else ""


class Recorder:
    """Wrap the SDK's ``_post`` to record every GraphQL request."""

    def __init__(self, client: InfrahubClientSync) -> None:
        self.rows: list[dict[str, Any]] = []
        original = client._post

        def post(url: str, payload: dict, *args: Any, **kwargs: Any) -> Any:
            start = time.perf_counter()
            response = original(url, payload, *args, **kwargs)
            query = payload.get("query", "") if isinstance(payload, dict) else ""
            selections = graphql_selections(query)
            self.rows.append(
                {
                    "bytes": len(response.content),
                    "seconds": time.perf_counter() - start,
                    "query_chars": len(query),
                    "query_fields": len(selections),
                    "root": selections[0] if selections else "",
                    "kind": "peer_get" if re.search(r"\bids:\s*\[", query) else "bulk",
                    "errors": "errors" in response.text[:200],
                    "query": query,
                    "selections": selections,
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
                "max_query_fields": max((r["query_fields"] for r in rows), default=0),
                "server_errors": sum(1 for r in rows if r["errors"]),
            }
        out["total_requests"] = len(self.rows)
        out["total_bytes"] = sum(r["bytes"] for r in self.rows)
        return out

    def queries(self) -> dict[str, dict[str, Any]]:
        """The first bulk query for every kind: its text, character count and selections."""
        captured: dict[str, dict[str, Any]] = {}
        for row in self.rows:
            if row["kind"] == "bulk" and row["root"] not in captured:
                captured[row["root"]] = {
                    "query_chars": row["query_chars"],
                    "query_fields": row["query_fields"],
                    "selections": row["selections"],
                    "query": row["query"],
                }
        return captured


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
    plan: Plan,
    schema_dict: Any,
    *,
    order: list[str],
    trace: bool = False,
    capture: bool = False,
) -> dict[str, Any]:
    client = new_client(address, token, branch, page)
    recorder = Recorder(client)
    harness = Harness(config=plan.config, client=client, schema=schema_dict, models=plan.models)
    if trace:
        tracemalloc.start()
    start = time.perf_counter()
    with selection(mode):
        for kind in order:
            harness.model_loader(model_name=kind, model=plan.models[kind])
    seconds = time.perf_counter() - start
    peak = 0
    if trace:
        peak = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()
    loaded = sum(len(list(harness.get_all(plan.models[k]))) for k in order)
    result = {"mode": mode, "seconds": round(seconds, 3), "objects_loaded": loaded, **recorder.summary()}
    if trace:
        result["peak_traced_mib"] = round(peak / 2**20, 1)
    if capture:
        result["queries"] = recorder.queries()
        result["peer_get_roots"] = sorted({r["root"] for r in recorder.rows if r["kind"] == "peer_get"})
    return result


def run_id_scan(
    address: str, token: str, branch: str, mode: str, page: int, plan: Plan, schema_dict: Any, *, capture: bool = False
) -> dict[str, Any]:
    client = new_client(address, token, branch, page)
    recorder = Recorder(client)
    harness = Harness(config=plan.config, client=client, schema=schema_dict, models=plan.models)
    start = time.perf_counter()
    count = 0
    with selection(mode):
        for kind in plan.dependency_order:
            count += sum(1 for _ in harness.list_existing_ids(kind))
    result = {"mode": mode, "seconds": round(time.perf_counter() - start, 3), "ids": count, **recorder.summary()}
    if capture:
        result["queries"] = recorder.queries()
    return result


def reread(address: str, token: str, branch: str, page: int, plan: Plan, schema_dict: Any) -> dict[str, Any]:
    """Fresh reread of a destination: per-kind counts and the relationship identities it retains."""
    client = new_client(address, token, branch, page)
    harness = Harness(config=plan.config, client=client, schema=schema_dict, models=plan.models)
    for kind in plan.dependency_order:
        harness.model_loader(model_name=kind, model=plan.models[kind])
    kinds: dict[str, Any] = {}
    for kind in plan.dependency_order:
        model = plan.models[kind]
        objects = list(harness.get_all(model))
        relationship_names = {r.name for r in plan.snapshot.kinds[kind].relationships}
        relationship_fields = [f for f in model._identifiers if f in relationship_names]
        entry: dict[str, Any] = {
            "adapter_count": len(objects),
            "sdk_count": client.count(kind=kind),
            "relationship_identifiers": relationship_fields,
            "distinct_identities": len({o.get_unique_id() for o in objects}),
        }
        if relationship_fields:
            entry["with_empty_relationship_identity"] = sum(
                1 for o in objects if any(not getattr(o, f, None) for f in relationship_fields)
            )
            entry["sample_identity"] = objects[0].get_unique_id() if objects else None
        kinds[kind] = entry
    return {
        "total_adapter": sum(v["adapter_count"] for v in kinds.values()),
        "total_sdk": sum(v["sdk_count"] for v in kinds.values()),
        "kinds": kinds,
    }


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


def measure(address: str, token: str, branch: str, args: argparse.Namespace, plan: Plan, schema_dict: Any) -> dict:
    """Run every comparison on one populated branch and return the report sections."""
    report: dict[str, Any] = {"projection": plan.projection(), "dependency_order": plan.dependency_order}
    order = plan.dependency_order
    # Warm-up so the server's own caches are in the same state for both variants.
    run_load(address, token, branch, "union", args.page, plan, schema_dict, order=order)
    runs: dict[str, list[dict[str, Any]]] = {"union": [], "attrs": []}
    for i in range(args.repeats):
        for mode in ("union", "attrs") if i % 2 == 0 else ("attrs", "union"):
            runs[mode].append(
                run_load(address, token, branch, mode, args.page, plan, schema_dict, order=order, capture=i == 0)
            )
    for mode in runs:
        runs[mode][-1]["_with_memory"] = run_load(
            address, token, branch, mode, args.page, plan, schema_dict, order=order, trace=True
        )
    report["runs"] = runs
    # An explicit configured `order` is returned unchanged by compute_order_and_tiers, so a user can
    # configure consumers before their peers. Reverse the dependency order to measure that case.
    reverse = plan.explicit_order(list(reversed(order)))
    report["explicit_reverse_order"] = {
        mode: [
            run_load(address, token, branch, mode, args.page, plan, schema_dict, order=reverse, capture=i == 0)
            for i in range(3)
        ]
        for mode in ("union", "attrs")
    }
    report["id_scan"] = {
        mode: [
            run_id_scan(address, token, branch, mode, args.page, plan, schema_dict, capture=i == 0) for i in range(3)
        ]
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
            "max_query_fields": rs[0]["bulk"]["max_query_fields"],
            "server_errors": sum(r["bulk"]["server_errors"] + r["peer_get"]["server_errors"] for r in rs),
            "peak_traced_mib": rs[-1]["_with_memory"].get("peak_traced_mib"),
        }
        for mode, rs in runs.items()
    }
    union_q = runs["union"][0]["queries"]
    attrs_q = runs["attrs"][0]["queries"]
    report["query_comparison"] = {
        kind: {
            "union_chars": union_q[kind]["query_chars"],
            "attrs_chars": attrs_q[kind]["query_chars"],
            "union_fields": union_q[kind]["query_fields"],
            "attrs_fields": attrs_q[kind]["query_fields"],
            "fields_only_in_union": sorted(set(union_q[kind]["selections"]) - set(attrs_q[kind]["selections"])),
        }
        for kind in union_q
        if kind in attrs_q
    }
    return report


def environment(address: str, token: str, report: dict[str, Any]) -> None:
    info = requests.get(f"{address}/api/info", headers={"X-INFRAHUB-KEY": token}, timeout=30).json()
    report["server_version"] = info.get("version")
    report["sdk_version"] = infrahub_sdk.__version__


def run_synthetic(args: argparse.Namespace, address: str, token: str) -> dict[str, Any]:
    branch = f"prefetch-study-{uuid.uuid4().hex[:8]}"
    admin = new_client(address, token, "main", args.page)
    admin.branch.create(branch_name=branch, sync_with_git=False)
    report: dict[str, Any] = {"scenario": "synthetic", "scale": args.scale, "page": args.page, "repeats": args.repeats}
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
        environment(address, token, report)
        t0 = time.perf_counter()
        report["populated"] = populate(client, branch, args.scale)
        report["objects"] = sum(report["populated"].values())
        report["populate_seconds"] = round(time.perf_counter() - t0, 1)
        schema_dict = client.schema.all(branch=branch)
        plan = Plan(synthetic_config(), schema_dict)
        report.update(measure(address, token, branch, args, plan, schema_dict))
        write_report(args.output, report)
    finally:
        try:
            admin.branch.delete(branch_name=branch)
        except GraphQLError as exc:
            # Neo4j's per-transaction memory cap can refuse a large branch delete; the study's
            # disposable stack is removed with its volumes, so report it rather than hide it.
            print(f"branch {branch} was not deleted: {exc.errors[0].get('message')}", file=sys.stderr)
        except ServerNotResponsiveError as exc:
            print(f"branch {branch} was not deleted: {exc}", file=sys.stderr)
    return report


def run_netbox(args: argparse.Namespace, address: str, token: str) -> dict[str, Any]:
    report: dict[str, Any] = {
        "scenario": "netbox",
        "branch": args.branch,
        "page": args.page,
        "repeats": args.repeats,
    }
    client = new_client(address, token, args.branch, args.page)
    environment(address, token, report)
    schema_dict = client.schema.all(branch=args.branch)
    plan = Plan(netbox_config(args.package), schema_dict)
    report["convergence_reread"] = reread(address, token, args.branch, args.page, plan, schema_dict)
    report["objects"] = report["convergence_reread"]["total_adapter"]
    report.update(measure(address, token, args.branch, args, plan, schema_dict))
    write_report(args.output, report)
    return report


def write_report(output: str, report: dict[str, Any]) -> None:
    with Path(output).open("w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, default=str)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", choices=("synthetic", "netbox"), default="synthetic")
    parser.add_argument("--scale", type=int, default=1)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--page", type=int, default=50)
    parser.add_argument("--package", help="netbox scenario: the package YAML whose mapping to use")
    parser.add_argument("--branch", help="netbox scenario: the populated destination branch to read")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    address = os.environ["INFRAHUB_ADDRESS"]
    token = os.environ["INFRAHUB_API_TOKEN"]
    if args.scenario == "netbox":
        if not (args.package and args.branch):
            parser.error("--scenario netbox needs --package and --branch")
        report = run_netbox(args, address, token)
    else:
        report = run_synthetic(args, address, token)
    json.dump(report["medians"], sys.stdout, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
