from collections import UserDict
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest

from infrahub_sync.cache.cursors import CursorState, CursorTier

if TYPE_CHECKING:
    from infrahub_sync.adapters.nautobot import NautobotAdapter

# The nautobot adapter hard-imports `pynautobot`, an optional dependency that is
# not part of the `dev` extra. Skip this module when it is unavailable instead
# of erroring during collection.
pynautobot = pytest.importorskip("pynautobot")


class _FakeRecord(UserDict):
    """`dict(MagicMock())` returns {}, so use UserDict to make `dict(node)` work."""


_DEFAULT_SETTINGS = {"url": "https://example.invalid", "token": "x"}


@pytest.fixture(autouse=True)
def _stubbed_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub the adapter client for each test and restore its factory afterward."""
    monkeypatch.setattr(
        "infrahub_sync.adapters.nautobot.NautobotAdapter._create_nautobot_client",
        lambda _self, _adapter: MagicMock(),
    )


def _make_adapter(mappings: list[dict], settings: dict | None = None) -> "NautobotAdapter":
    """Build a NautobotAdapter with stubbed pynautobot client.

    `settings` defaults to the url/token pair every pre-existing test used, so
    an omitted `depth` stays the default here exactly as it is in the field.
    """
    from infrahub_sync import SchemaMappingModel, SyncAdapter, SyncConfig
    from infrahub_sync.adapters.nautobot import NautobotAdapter

    schema_mapping = [SchemaMappingModel(**m) for m in mappings]
    config = SyncConfig(
        name="t",
        source=SyncAdapter(name="nautobot"),
        destination=SyncAdapter(name="infrahub"),
        schema_mapping=schema_mapping,
    )
    adapter_settings = SyncAdapter(
        name="nautobot",
        settings=_DEFAULT_SETTINGS if settings is None else settings,
    )
    return NautobotAdapter(target="test", adapter=adapter_settings, config=config)


def test_cursor_tier_is_timestamp_for_mapped_kinds() -> None:
    adapter = _make_adapter([{"name": "InfraDevice", "mapping": "dcim.devices"}])
    assert adapter.cursor_tier_for("InfraDevice") is CursorTier.TIMESTAMP


def test_cursor_tier_is_none_for_unmapped_kinds() -> None:
    adapter = _make_adapter([{"name": "InfraDevice", "mapping": "dcim.devices"}])
    assert adapter.cursor_tier_for("Unknown") is CursorTier.NONE


def test_cursor_tier_is_none_for_empty_mapping() -> None:
    adapter = _make_adapter([{"name": "InfraDevice", "mapping": ""}])
    assert adapter.cursor_tier_for("InfraDevice") is CursorTier.NONE


def test_list_changed_since_uses_last_updated_filter() -> None:
    adapter = _make_adapter([{"name": "InfraDevice", "mapping": "dcim.devices", "identifiers": ["name"]}])
    fake_record = _FakeRecord({"id": 1, "name": "leaf1"})
    fake_endpoint = MagicMock()
    fake_endpoint.filter.return_value = [fake_record]
    adapter.client.dcim.devices = fake_endpoint  # ty: ignore[unresolved-attribute]

    fake_model = MagicMock()
    fake_model.filter_records.side_effect = lambda records, **_kw: records
    fake_model.transform_records.side_effect = lambda records, **_kw: records
    adapter.InfraDevice = fake_model  # ty: ignore[unresolved-attribute]

    cursor = CursorState(tier=CursorTier.TIMESTAMP, value="2026-05-17T10:00:00Z")
    rows = list(adapter.list_changed_since("InfraDevice", cursor))

    fake_endpoint.filter.assert_called_once_with(last_updated__gte="2026-05-17T10:00:00Z")
    assert rows[0]["local_id"] == "1"


def test_list_changed_since_raises_for_unknown_model() -> None:
    adapter = _make_adapter([{"name": "InfraDevice", "mapping": "dcim.devices"}])
    with pytest.raises(NotImplementedError):
        list(
            adapter.list_changed_since("Unknown", CursorState(tier=CursorTier.TIMESTAMP, value="2026-01-01T00:00:00Z"))
        )


def test_list_changed_since_falls_back_when_endpoint_rejects_filter() -> None:
    """Some Nautobot endpoints (front-ports, rear-ports, ...) return 400 'Unknown filter field'
    on `last_updated__gte`. The adapter must catch that and fall back to `endpoint.all()`.
    """
    import pynautobot.core.query  # ty: ignore[unresolved-import]  # optional dep, absent on the Python 3.10 profile

    adapter = _make_adapter([{"name": "InfraDevice", "mapping": "dcim.devices", "identifiers": ["name"]}])
    fake_record = _FakeRecord({"id": 7, "name": "edge1"})

    fake_endpoint = MagicMock()
    # Build a real RequestError mirroring what pynautobot raises on 400.
    fake_resp = MagicMock()
    fake_resp.status_code = 400
    fake_resp.reason = "Bad Request"
    fake_resp.json.return_value = {"last_updated__gte": ["Unknown filter field"]}
    fake_resp.url = "https://demo.nautobot.com/api/dcim/devices/?last_updated__gte=…"
    fake_resp.text = ""
    fake_resp.request.body = None
    fake_endpoint.filter.side_effect = pynautobot.core.query.RequestError(fake_resp)
    fake_endpoint.all.return_value = [fake_record]
    adapter.client.dcim.devices = fake_endpoint  # ty: ignore[unresolved-attribute]

    fake_model = MagicMock()
    fake_model.filter_records.side_effect = lambda records, **_kw: records
    fake_model.transform_records.side_effect = lambda records, **_kw: records
    adapter.InfraDevice = fake_model  # ty: ignore[unresolved-attribute]

    cursor = CursorState(tier=CursorTier.TIMESTAMP, value="2026-05-17T10:00:00Z")
    rows = list(adapter.list_changed_since("InfraDevice", cursor))

    fake_endpoint.filter.assert_called_once_with(last_updated__gte="2026-05-17T10:00:00Z")
    fake_endpoint.all.assert_called_once_with()
    assert rows[0]["local_id"] == "7"


def test_list_existing_ids_returns_unique_ids() -> None:
    adapter = _make_adapter([{"name": "InfraDevice", "mapping": "dcim.devices", "identifiers": ["name"]}])
    rec_a = _FakeRecord({"id": 1, "name": "leaf1"})
    rec_b = _FakeRecord({"id": 2, "name": "leaf2"})
    fake_endpoint = MagicMock()
    fake_endpoint.all.return_value = [rec_a, rec_b]
    adapter.client.dcim.devices = fake_endpoint  # ty: ignore[unresolved-attribute]

    fake_model = MagicMock()
    fake_model.filter_records.side_effect = lambda records, **_kw: records
    fake_model.transform_records.side_effect = lambda records, **_kw: records

    def _make_instance(**payload: object) -> MagicMock:
        instance = MagicMock()
        instance.get_unique_id.return_value = payload["local_id"]
        return instance

    fake_model.side_effect = _make_instance
    adapter.InfraDevice = fake_model  # ty: ignore[unresolved-attribute]

    ids = list(adapter.list_existing_ids("InfraDevice"))
    fake_endpoint.all.assert_called_once_with()
    assert ids == ["1", "2"]


# --- operator-selected `depth` ------------------------------------------------
#
# Observed on Nautobot 2.4.41: a depth-0 list response carries a nested object as
# {id, object_type, url} only. `pynautobot.Record.__iter__` recurses over the keys
# actually present, so `dict(node)` stays shallow and `device_type.model` is
# unreachable. The operator opts into `depth` to widen the response.

_DEVICE_ID = "11111111-1111-1111-1111-111111111111"
_DEVICE_TYPE_ID = "22222222-2222-2222-2222-222222222222"
_NAUTOBOT_API = "https://demo.nautobot.invalid/api"

_DEPTH0_DEVICE = {
    "id": _DEVICE_ID,
    "object_type": "dcim.device",
    "url": f"{_NAUTOBOT_API}/dcim/devices/{_DEVICE_ID}/",
    "name": "ams01-edge-01",
    "device_type": {
        "id": _DEVICE_TYPE_ID,
        "object_type": "dcim.devicetype",
        "url": f"{_NAUTOBOT_API}/dcim/device-types/{_DEVICE_TYPE_ID}/",
    },
}

_DEPTH1_DEVICE = {
    **_DEPTH0_DEVICE,
    "device_type": {
        **_DEPTH0_DEVICE["device_type"],
        "display": "Juniper mx-100",
        "model": "mx-100",
    },
}

_DEVICE_MAPPING = [
    {
        "name": "TestingDevice",
        "mapping": "dcim.devices",
        "identifiers": ["name"],
        "fields": [
            {"name": "name", "mapping": "name"},
            {"name": "model", "mapping": "device_type.model"},
        ],
    }
]


def _settings_with_depth(depth: object) -> dict:
    return {**_DEFAULT_SETTINGS, "depth": depth}


class _NoFetchApi:
    """`pynautobot.api` stand-in that refuses every HTTP call.

    `Record.__getattr__` falls back to `full_details()`, which reads
    `api.http_session`. Counting and raising there turns any lazy per-record
    fetch into a visible failure instead of letting it quietly supply the value
    under test.
    """

    token = "does-not-matter"  # noqa: S105
    api_version = None
    default_filters = None
    base_url = _NAUTOBOT_API

    def __init__(self) -> None:
        self.http_session_reads = 0

    @property
    def http_session(self) -> object:
        self.http_session_reads += 1
        msg = "the adapter attempted a lazy detail fetch"
        raise AssertionError(msg)


def _real_record(payload: dict, api: _NoFetchApi) -> Any:  # noqa: ANN401 — a real pynautobot Record, optional dep
    """Build a genuine pynautobot Record — the object the adapter really receives."""
    return pynautobot.core.response.Record(payload, api, None)


def _testing_device_model() -> Any:  # noqa: ANN401 — a locally built DiffSync model class
    """A real NautobotModel whose `model` attribute is required, like the failing kind."""
    from infrahub_sync.adapters.nautobot import NautobotModel

    class _TestingDevice(NautobotModel):
        _modelname = "TestingDevice"
        _identifiers = ("name",)
        _attributes = ("model",)

        name: str
        model: str

    return _TestingDevice


def _device_endpoint(adapter: "NautobotAdapter", records: list) -> MagicMock:
    endpoint = MagicMock()
    endpoint.all.return_value = records
    adapter.client.dcim.devices = endpoint  # ty: ignore[unresolved-attribute]
    return endpoint


def test_depth0_record_hides_the_nested_model_and_fails_validation() -> None:
    """The defect: the real shallow shape never reaches `device_type.model`."""
    from pydantic import ValidationError

    api = _NoFetchApi()
    record = _real_record(_DEPTH0_DEVICE, api)
    assert "model" not in dict(record)["device_type"]

    adapter = _make_adapter(_DEVICE_MAPPING)
    _device_endpoint(adapter, [record])
    model = _testing_device_model()
    adapter.TestingDevice = model  # ty: ignore[unresolved-attribute]

    with pytest.raises(ValidationError, match="model"):
        adapter.model_loader("TestingDevice", model)
    assert api.http_session_reads == 0


def test_depth1_model_loader_sends_depth_and_resolves_the_nested_model() -> None:
    """Site 4 (`model_loader`) plus the end-to-end payoff of the setting."""
    api = _NoFetchApi()
    record = _real_record(_DEPTH1_DEVICE, api)

    adapter = _make_adapter(_DEVICE_MAPPING, settings=_settings_with_depth(1))
    endpoint = _device_endpoint(adapter, [record])
    model = _testing_device_model()
    adapter.TestingDevice = model  # ty: ignore[unresolved-attribute]

    adapter.model_loader("TestingDevice", model)

    endpoint.all.assert_called_once_with(depth=1)
    assert adapter.get(model, "ams01-edge-01").model_dump()["model"] == "mx-100"
    assert api.http_session_reads == 0


def test_depth1_is_sent_on_the_incremental_filter() -> None:
    """Site 1: `list_changed_since` filter."""
    api = _NoFetchApi()
    adapter = _make_adapter(_DEVICE_MAPPING, settings=_settings_with_depth(1))
    endpoint = _device_endpoint(adapter, [])
    endpoint.filter.return_value = [_real_record(_DEPTH1_DEVICE, api)]
    adapter.TestingDevice = _testing_device_model()  # ty: ignore[unresolved-attribute]

    cursor = CursorState(tier=CursorTier.TIMESTAMP, value="2026-05-17T10:00:00Z")
    rows = list(adapter.list_changed_since("TestingDevice", cursor))

    endpoint.filter.assert_called_once_with(last_updated__gte="2026-05-17T10:00:00Z", depth=1)
    assert rows[0]["model"] == "mx-100"
    assert api.http_session_reads == 0


def test_depth1_is_sent_on_the_unknown_filter_fallback() -> None:
    """Site 2: the full-extract fallback taken when `last_updated__gte` is rejected."""
    api = _NoFetchApi()
    adapter = _make_adapter(_DEVICE_MAPPING, settings=_settings_with_depth(1))
    endpoint = _device_endpoint(adapter, [_real_record(_DEPTH1_DEVICE, api)])

    fake_resp = MagicMock()
    fake_resp.status_code = 400
    fake_resp.reason = "Bad Request"
    fake_resp.json.return_value = {"last_updated__gte": ["Unknown filter field"]}
    fake_resp.url = f"{_NAUTOBOT_API}/dcim/devices/?last_updated__gte=…"
    fake_resp.text = ""
    fake_resp.request.body = None
    endpoint.filter.side_effect = pynautobot.core.query.RequestError(fake_resp)
    adapter.TestingDevice = _testing_device_model()  # ty: ignore[unresolved-attribute]

    cursor = CursorState(tier=CursorTier.TIMESTAMP, value="2026-05-17T10:00:00Z")
    rows = list(adapter.list_changed_since("TestingDevice", cursor))

    endpoint.filter.assert_called_once_with(last_updated__gte="2026-05-17T10:00:00Z", depth=1)
    endpoint.all.assert_called_once_with(depth=1)
    assert rows[0]["model"] == "mx-100"
    assert api.http_session_reads == 0


def test_depth1_is_sent_on_list_existing_ids() -> None:
    """Site 3: the soft-delete sweep, which builds the same pydantic model."""
    api = _NoFetchApi()
    adapter = _make_adapter(_DEVICE_MAPPING, settings=_settings_with_depth(1))
    endpoint = _device_endpoint(adapter, [_real_record(_DEPTH1_DEVICE, api)])
    adapter.TestingDevice = _testing_device_model()  # ty: ignore[unresolved-attribute]

    ids = list(adapter.list_existing_ids("TestingDevice"))

    endpoint.all.assert_called_once_with(depth=1)
    assert ids == ["ams01-edge-01"]
    assert api.http_session_reads == 0


def test_explicit_depth_zero_is_sent() -> None:
    """Present always means sent, including the value that matches the server default."""
    adapter = _make_adapter(_DEVICE_MAPPING, settings=_settings_with_depth(0))
    endpoint = _device_endpoint(adapter, [])
    adapter.TestingDevice = _testing_device_model()  # ty: ignore[unresolved-attribute]

    adapter.model_loader("TestingDevice", adapter.TestingDevice)  # ty: ignore[unresolved-attribute]

    endpoint.all.assert_called_once_with(depth=0)


@pytest.mark.parametrize(
    "settings",
    [
        pytest.param(dict(_DEFAULT_SETTINGS), id="missing-key"),
        pytest.param(_settings_with_depth(None), id="yaml-null"),
    ],
)
def test_absent_depth_sends_no_parameter(settings: dict) -> None:
    """A missing key and an explicit YAML null are both absent: no parameter at all."""
    adapter = _make_adapter(_DEVICE_MAPPING, settings=settings)
    endpoint = _device_endpoint(adapter, [])
    adapter.TestingDevice = _testing_device_model()  # ty: ignore[unresolved-attribute]

    adapter.model_loader("TestingDevice", adapter.TestingDevice)  # ty: ignore[unresolved-attribute]

    endpoint.all.assert_called_once_with()


@pytest.mark.parametrize(
    "bad_depth",
    [
        pytest.param("1", id="yaml-string"),
        pytest.param(True, id="bool"),
        pytest.param(1.0, id="float"),
        pytest.param(-1, id="below-range"),
        pytest.param(11, id="above-range"),
    ],
)
def test_invalid_depth_is_refused_at_construction(bad_depth: object) -> None:
    """Refused before any request is built. No clamp, no coercion."""
    with pytest.raises(ValueError) as excinfo:
        _make_adapter(_DEVICE_MAPPING, settings=_settings_with_depth(bad_depth))

    message = str(excinfo.value)
    assert "depth" in message
    assert repr(bad_depth) in message


def test_depth_decides_the_payload_the_endpoint_returns() -> None:
    """Ties the pass-through to its consequence.

    The fake endpoint answers like Nautobot does: the wide payload only when
    `depth` is on the request. Drop the pass-through and this fails on the
    required `model` attribute, not merely on a call-shape assertion.
    """
    api = _NoFetchApi()

    def _all(**kwargs: object) -> list:
        payload = _DEPTH1_DEVICE if kwargs.get("depth") == 1 else _DEPTH0_DEVICE
        return [_real_record(payload, api)]

    adapter = _make_adapter(_DEVICE_MAPPING, settings=_settings_with_depth(1))
    endpoint = _device_endpoint(adapter, [])
    endpoint.all.side_effect = _all
    model = _testing_device_model()
    adapter.TestingDevice = model  # ty: ignore[unresolved-attribute]

    adapter.model_loader("TestingDevice", model)

    assert adapter.get(model, "ams01-edge-01").model_dump()["model"] == "mx-100"
    assert api.http_session_reads == 0
