from __future__ import annotations

import os
import re
from typing import TYPE_CHECKING, Any

from prometheus_client.parser import text_string_to_metric_families

try:
    from typing import Self
except ImportError:
    from typing_extensions import Self

import requests
from diffsync import Adapter, DiffSyncModel

from infrahub_sync import (
    DiffSyncMixin,
    DiffSyncModelMixin,
    SchemaMappingModel,
    SyncAdapter,
    SyncConfig,
)


# ---- If you already have these in your project, you can delete the fallbacks below and import instead. ----
def _dotted_get(obj: Any, path: str) -> Any:
    """Safe dotted getter. Supports 'labels.domain', 'join.metric.labels.foo'."""
    cur = obj
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
        if cur is None:
            return None
    return cur


def _derive_identifier_key(obj: dict) -> str:
    """Deterministic local_id from metric name + sorted labels; fallback to hashable value."""
    name = obj.get("__metric__", "")
    labels = obj.get("labels", {}) or {}
    key = ",".join(f"{k}={labels[k]}" for k in sorted(labels)) if isinstance(labels, dict) else str(labels)
    return f"{name}|{key}" if key else name


if TYPE_CHECKING:
    from collections.abc import Mapping
# -----------------------------------------------------------------------------------------------------------


# ======================================
# Clients: scrape & Prometheus HTTP API
# ======================================


class PrometheusScrapeClient:
    """
    Scrape a Prometheus *text exposition* endpoint (e.g., http://host:9100/metrics).
    Returns dict: { metric_name: [sample, ...] } where sample is:
      {
        "__metric__": "<name>",
        "labels": { ... },
        "value": float,
        "timestamp": float|None,
        "help": str,
        "type": str,
      }
    """

    def __init__(
        self,
        base_url: str,
        endpoint: str = "/metrics",
        auth_method: str = "none",  # "none" | "basic" | "bearer"
        username: str | None = None,
        password: str | None = None,
        api_token: str | None = None,
        timeout: float | None = 10,
        verify: bool = True,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.endpoint = endpoint if endpoint.startswith("/") else f"/{endpoint}"
        self.auth_method = auth_method
        self.username = username
        self.password = password
        self.api_token = api_token
        self.timeout = timeout
        self.verify = verify
        self.headers = dict(headers or {})
        if self.auth_method == "bearer" and self.api_token:
            self.headers.setdefault("Authorization", f"Bearer {self.api_token}")

    def _auth(self):
        if self.auth_method == "basic" and self.username is not None:
            return (self.username, self.password or "")
        return None

    def get_metrics(self, params: dict[str, Any] | None = None) -> dict[str, list[dict]] | None:
        url = f"{self.base_url}{self.endpoint}"
        resp = requests.get(
            url, params=params or {}, timeout=self.timeout, verify=self.verify, headers=self.headers, auth=self._auth()
        )
        resp.raise_for_status()
        text = resp.text
        return parse_prometheus_text(text)


class PrometheusAPIClient:
    """
    Query the *Prometheus HTTP API* (e.g., http://prometheus:9090/api/v1/query).
    Use this if you want to run PromQL (including joins) and map the result to records.

    For each resource, call .instant_query(query) and normalize to the same 'sample' shape:
      {
        "__metric__": <__name__ label or resource alias>,
        "labels": { ... },   # labels from PromQL result
        "value": float,
        "timestamp": float|None,
        "help": "",
        "type": "vector",
      }
    """

    def __init__(
        self,
        base_url: str,
        endpoint: str = "/api/v1/query",
        timeout: float | None = 10,
        verify: bool = True,
        headers: dict[str, str] | None = None,
        auth_method: str = "none",
        username: str | None = None,
        password: str | None = None,
        api_token: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.endpoint = endpoint if endpoint.startswith("/") else f"/{endpoint}"
        self.timeout = timeout
        self.verify = verify
        self.headers = dict(headers or {})
        self.auth_method = auth_method
        self.username = username
        self.password = password
        self.api_token = api_token
        if self.auth_method == "bearer" and self.api_token:
            self.headers.setdefault("Authorization", f"Bearer {self.api_token}")

    def _auth(self):
        if self.auth_method == "basic" and self.username is not None:
            return (self.username, self.password or "")
        return None

    def instant_query(self, query: str) -> list[dict[str, Any]]:
        """Run an instant vector query and normalize results to 'sample' dicts."""
        url = f"{self.base_url}{self.endpoint}"
        params = {"query": query}
        resp = requests.get(
            url, params=params, timeout=self.timeout, verify=self.verify, headers=self.headers, auth=self._auth()
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != "success":
            msg = f"Prometheus API error: {data!r}"
            raise ValueError(msg)

        payload = data.get("data", {})
        if payload.get("resultType") not in ("vector", "scalar"):
            # For adapter simplicity; extend as needed.
            msg = f"Unsupported result type: {payload.get('resultType')}"
            raise ValueError(msg)

        results = []
        if payload.get("resultType") == "scalar":
            ts, val = payload.get("result", [None, None])
            results.append(
                {
                    "__metric__": "",
                    "labels": {},
                    "value": float(val) if val is not None else float("nan"),
                    "timestamp": float(ts) if ts is not None else None,
                    "help": "",
                    "type": "vector",
                }
            )
            return results

        for entry in payload.get("result", []):
            labels = dict(entry.get("metric") or {})
            ts, val = entry.get("value", [None, None])
            # Prefer __name__ if present, else leave empty (will be overwritten by resource alias)
            metric_name = labels.pop("__name__", "")
            results.append(
                {
                    "__metric__": metric_name,
                    "labels": labels,
                    "value": float(val) if val is not None else float("nan"),
                    "timestamp": float(ts) if ts is not None else None,
                    "help": "",
                    "type": "vector",
                }
            )
        return results


def parse_prometheus_text(text: str) -> dict[str, list[dict[str, Any]]]:
    """
    Parse Prometheus exposition format using prometheus_client.parser
    and normalize to a dict of samples keyed by metric name.
    """
    families = list(text_string_to_metric_families(text))
    out: dict[str, list[dict[str, Any]]] = {}
    meta: dict[str, tuple[str, str]] = {}  # name -> (help, type)

    for fam in families:
        meta[fam.name] = (fam.documentation or "", fam.type or "")
        for sample in fam.samples:
            # prometheus_client returns tuples; normalize
            # Common tuple shape: (name, labels, value, timestamp, exemplar)
            name, labels, value, timestamp = sample[0], sample[1], sample[2], None
            if len(sample) > 3:
                timestamp = sample[3]
            ts = None
            if timestamp is not None:
                # prometheus_client may give ns; heuristically convert if too big
                ts = (
                    (timestamp / 1000.0)
                    if (isinstance(timestamp, (int, float)) and timestamp > 10**10)
                    else float(timestamp)
                )
            rec = {
                "__metric__": name,
                "labels": dict(labels or {}),
                "value": float(value),
                "timestamp": ts,
                "help": meta.get(name, ("", ""))[0],
                "type": meta.get(name, ("", ""))[1],
            }
            out.setdefault(name, []).append(rec)
    return out


_LOOKUP_RE = re.compile(
    r"""^lookup\(\s*
        (?P<metric>[^,\s]+)\s*,\s*
        (?P<key_path>[^,\s][^,]*?)\s*,\s*
        (?P<value_path>[^,\s][^,]*?)
        (?:\s*,\s*(?P<default>.+?)\s*)?
        \)$""",
    re.VERBOSE,
)


class LookupResolver:
    """
    Resolves lookup(metric, key_path, value_path, default?) against a pool of samples.
    Caches indexes per (metric, key_path).
    """

    def __init__(self, samples_by_metric: dict[str, list[dict[str, Any]]]) -> None:
        self.samples_by_metric = samples_by_metric
        self._index_cache: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
        # extra cache keyed by (metric, label_name) for direct value lookups
        self._label_index_cache: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}

    def resolve(self, current_obj: dict[str, Any], expr: str) -> Any:
        m = _LOOKUP_RE.match(expr.strip())
        if not m:
            return None
        metric = m.group("metric")
        key_path = m.group("key_path").strip()
        value_path = m.group("value_path").strip()
        default_raw = m.group("default")
        default = None
        if default_raw is not None:
            default_raw = default_raw.strip()
            # strip quotes if present
            if (default_raw.startswith('"') and default_raw.endswith('"')) or (
                default_raw.startswith("'") and default_raw.endswith("'")
            ):
                default = default_raw[1:-1]
            else:
                default = default_raw

        # Build or reuse index by path
        idx_key = (metric, key_path)
        if idx_key not in self._index_cache:
            index: dict[str, dict[str, Any]] = {}
            for rec in self.samples_by_metric.get(metric, []):
                k = _dotted_get(rec, key_path)
                if k is None:
                    continue
                index[str(k)] = rec
            self._index_cache[idx_key] = index

        # Find key from current object
        left_key = _dotted_get(current_obj, key_path)
        if left_key is None:
            return default

        match = self._index_cache[idx_key].get(str(left_key))
        if not match:
            return default

        val = _dotted_get(match, value_path)
        return default if val is None else val

    def resolve_fn(
        self,
        current_obj: dict[str, Any],
        metric: str,
        key_or_path: Any,
        value_path: str,
        default: Any = None,
    ) -> Any:
        """
        If key_or_path is a dotted path (e.g. 'labels.dialer_name'), resolve via that path.
        Otherwise treat it as the already-resolved key value and match on the common label
        'dialer_name' (works for this use-case).
        """
        if key_or_path in (None, "", "*", {}):
            for rec in self.samples_by_metric.get(metric, []):
                val = _dotted_get(rec, value_path)
                if val is not None:
                    return val
            return default

        # If a dotted string path is provided, use the strict joiner
        if isinstance(key_or_path, str) and "." in key_or_path:
            return self.resolve(current_obj, f"lookup({metric},{key_or_path},{value_path},{default!r})")

        # Existing "treat as direct key value" path (kept for backwards-compat)
        key_value = key_or_path
        label_name = "dialer_name"
        lidx_key = (metric, label_name)
        if lidx_key not in self._label_index_cache:
            idx: dict[str, dict[str, Any]] = {}
            for rec in self.samples_by_metric.get(metric, []):
                lbls = rec.get("labels", {}) or {}
                if label_name in lbls:
                    idx[str(lbls[label_name])] = rec
            self._label_index_cache[lidx_key] = idx

        match = self._label_index_cache[lidx_key].get(str(key_value))
        if not match:
            return default
        val = _dotted_get(match, value_path)
        return default if val is None else val


class PrometheusAdapter(DiffSyncMixin, Adapter):
    """
    Two modes:
      - Scrape mode (default): settings.mode="scrape", settings.url="http://host:port", settings.endpoint="/metrics"
      - API mode (Prometheus HTTP API): settings.mode="api", settings.url="http://prometheus:9090",
          settings.promql.resources: { <resource_name>: "<promql query>", ... }

    In both modes, we normalize to samples and expose them to schema_mapping where:
      - schema_mapping[*].mapping == <metric or resource_name> to load records from
      - fields[*].mapping can be:
          * "labels.<key>", "value", "help", "type", "timestamp"
          * or lookup(...) to pull from ANY other metric/resource by key
    """

    type = "Prometheus"

    def __init__(self, target: str, adapter: SyncAdapter, config: SyncConfig, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.target = target
        self.config = config
        settings = adapter.settings or {}

        # Mode selection
        self.mode = (settings.get("mode") or "scrape").lower()  # "scrape" | "api"

        # Common settings
        self.timeout = settings.get("timeout", 10)
        self.verify_ssl = settings.get("verify_ssl", True)
        self.params = settings.get("params", {})

        # Auth (both modes)
        self.auth_method = settings.get("auth_method", "none")
        self.username = os.environ.get("PROM_USERNAME") or settings.get("username")
        self.password = os.environ.get("PROM_PASSWORD") or settings.get("password")
        self.api_token = os.environ.get("PROM_TOKEN") or settings.get("token")
        self.headers = settings.get("headers", {})

        # URL/endpoint
        self.url = os.environ.get("PROM_URL") or os.environ.get("PROM_ADDRESS") or settings.get("url")
        if not self.url:
            msg = "Prometheus 'url' must be specified in settings.url (or PROM_URL)."
            raise ValueError(msg)

        if self.mode == "scrape":
            endpoint = settings.get("endpoint", "/metrics")
            self.client = PrometheusScrapeClient(
                base_url=self.url,
                endpoint=endpoint,
                auth_method=self.auth_method,
                username=self.username,
                password=self.password,
                api_token=self.api_token,
                timeout=self.timeout,
                verify=self.verify_ssl,
                headers=self.headers,
            )
        elif self.mode == "api":
            endpoint = settings.get("endpoint", "/api/v1/query")
            self.client = PrometheusAPIClient(
                base_url=self.url,
                endpoint=endpoint,
                timeout=self.timeout,
                verify=self.verify_ssl,
                headers=self.headers,
                auth_method=self.auth_method,
                username=self.username,
                password=self.password,
                api_token=self.api_token,
            )
            # promql resources: { resource_name: "<promql query>" }
            self.promql_resources: dict[str, str] = settings.get("promql", {}).get("resources", {})
            if not isinstance(self.promql_resources, dict):
                msg = "settings.promql.resources must be a mapping of resource_name -> query string"
                raise ValueError(msg)
        else:
            msg = f"Unknown mode '{self.mode}'. Use 'scrape' or 'api'."
            raise ValueError(msg)

        self._samples_by_metric: dict[str, list[dict[str, Any]]] | None = None
        self._lookup: LookupResolver | None = None

    def _ensure_samples(self) -> dict[str, list[dict[str, Any]]]:
        if self._samples_by_metric is not None:
            if self._lookup is None:
                self._lookup = LookupResolver(samples_by_metric=self._samples_by_metric)
            return self._samples_by_metric

        if isinstance(self.client, PrometheusScrapeClient):
            self._samples_by_metric = self.client.get_metrics(params=self.params)
        else:
            # API mode: build resources by querying PromQL
            store: dict[str, list[dict[str, Any]]] = {}
            for resource_name, query in self.promql_resources.items():
                try:
                    results = self.client.instant_query(query)  # type: ignore[attr-defined]
                except Exception as exc:
                    msg = f"Prometheus API query failed for '{resource_name}': {exc!s}"
                    raise ValueError(msg) from exc
                # Normalize: ensure __metric__ present and set to resource_name if empty
                normalized = []
                for r in results:
                    rec = dict(r)
                    if not rec.get("__metric__"):
                        rec["__metric__"] = resource_name
                    normalized.append(rec)
                store[resource_name] = normalized
            self._samples_by_metric = store

        # init lookup resolver
        self._lookup = LookupResolver(samples_by_metric=self._samples_by_metric)
        return self._samples_by_metric

    # ---- DiffSync hooks ----

    def model_loader(self, model_name: str, model) -> None:
        samples_by_metric = self._ensure_samples()

        for element in self.config.schema_mapping:
            if element.name != model_name:
                continue

            metric_or_resource = element.mapping
            objs = samples_by_metric.get(metric_or_resource, [])
            total = len(objs)

            # Inject a callable 'lookup' into each record so Jinja transforms can use it.
            # NOTE: must be done BEFORE transforms.
            if self._lookup:
                for obj in objs:
                    # bind current obj into the callable
                    def _mk_lookup(current: dict[str, Any]):
                        return lambda metric, key_or_path, value_path, default=None: self._lookup.resolve_fn(
                            current, metric, key_or_path, value_path, default
                        )

                    obj["lookup"] = _mk_lookup(obj)

            if self.config.source.name.title() == self.type.title():
                filtered_objs = model.filter_records(records=objs, schema_mapping=element)
                transformed_objs = model.transform_records(records=filtered_objs, schema_mapping=element)
                print(
                    f"{self.type}: Loading {len(transformed_objs)}/{total} from '{metric_or_resource}' (with transforms)"
                )
            else:
                transformed_objs = objs
                print(f"{self.type}: Loading all {total} from '{metric_or_resource}'")

            for obj in transformed_objs:
                data = self.obj_to_diffsync(obj=obj, mapping=element, model=model)
                item = model(**data)
                self.add(item)

    def obj_to_diffsync(self, obj: dict[str, Any], mapping: SchemaMappingModel, model: PrometheusModel) -> dict:
        local_id = _derive_identifier_key(obj)
        data: dict[str, Any] = {"local_id": str(local_id)}

        for field in mapping.fields or []:
            field_is_list = hasattr(model, "is_list") and model.is_list(name=field.name)

            if field.static is not None:
                data[field.name] = field.static
                continue

            if field.mapping and not field_is_list and not field.reference:
                m = field.mapping.strip()

                # Syntax: lookup(metric, key_path_or_value, value_path [, default])
                if m.startswith("lookup("):
                    value = self._lookup.resolve(obj, m) if self._lookup else None
                    if value is not None:
                        data[field.name] = value
                    continue

                value = _dotted_get(obj, m)
                if value is not None:
                    data[field.name] = value
                continue

            if field_is_list and field.mapping and not field.reference:
                msg = "list attribute with a simple mapping is not supported yet."
                raise NotImplementedError(msg)

            if field.mapping and field.reference:
                # Reference resolution would use your store; left as-is/an exercise.
                ref_value = _dotted_get(obj, field.mapping)
                if ref_value is not None:
                    data[field.name] = ref_value

        return data


class PrometheusModel(DiffSyncModelMixin, DiffSyncModel):
    @classmethod
    def create(
        cls,
        adapter: Adapter,
        ids: Mapping[Any, Any],
        attrs: Mapping[Any, Any],
    ) -> Self | None:
        # TODO: To implement
        return super().create(adapter=adapter, ids=ids, attrs=attrs)

    def update(self, attrs: dict) -> Self | None:
        # TODO: To implement
        return super().update(attrs)
