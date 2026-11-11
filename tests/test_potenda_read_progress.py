"""Potenda reports when each side's read starts and ends (spec 018, research R1, R2, R5, R7, R8).

The adapters are real DiffSync adapters with an in-memory store, so the node counts come from
the same `Adapter.count` the engine uses. The observer only records what it receives.
"""

from __future__ import annotations

import logging
import pickle  # noqa: S403 - the tests pickle errors they built themselves
import threading
from typing import TYPE_CHECKING, ClassVar

import pytest
from diffsync import Adapter, DiffSyncModel

from infrahub_sync.potenda import Potenda, SideLoadError
from infrahub_sync.potenda.read_progress import SideReadReport

if TYPE_CHECKING:
    from infrahub_sync.potenda.read_progress import Side

_SECRET_DETAIL = "db.internal.example:5432 password=ZZ-READ-CANARY"  # noqa: S105 — a canary, not a credential


class DcimDevice(DiffSyncModel):
    """A device node."""

    _modelname = "DcimDevice"
    _identifiers = ("name",)
    _attributes = ()
    name: str


class LocationSite(DiffSyncModel):
    """A site node."""

    _modelname = "LocationSite"
    _identifiers = ("name",)
    _attributes = ()
    name: str


class IpamPrefix(DiffSyncModel):
    """A prefix node, which the tests leave empty."""

    _modelname = "IpamPrefix"
    _identifiers = ("prefix",)
    _attributes = ()
    prefix: str


class _FakeAdapter(Adapter):
    """A DiffSync adapter that adds a fixed number of nodes, or raises, when it loads."""

    DcimDevice = DcimDevice
    LocationSite = LocationSite
    IpamPrefix = IpamPrefix
    top_level: ClassVar[list[str]] = []

    def __init__(self, *, devices: int = 0, sites: int = 0, error: BaseException | None = None) -> None:
        super().__init__()
        self._devices = devices
        self._sites = sites
        self._error = error

    def load(self) -> None:
        """Add the configured nodes, or raise the configured error."""
        if self._error is not None:
            raise self._error
        for index in range(self._devices):
            self.add(DcimDevice(name=f"device-{index}"))
        for index in range(self._sites):
            self.add(LocationSite(name=f"site-{index}"))


class _RecordingObserver:
    """Records every call it receives, in order."""

    def __init__(self) -> None:
        self.events: list[tuple[str, Side, SideReadReport | None]] = []
        self._lock = threading.Lock()

    def read_started(self, side: Side) -> None:
        with self._lock:
            self.events.append(("started", side, None))

    def read_ended(self, side: Side, report: SideReadReport) -> None:
        with self._lock:
            self.events.append(("ended", side, report))

    def for_side(self, side: Side) -> list[tuple[str, SideReadReport | None]]:
        return [(name, report) for name, event_side, report in self.events if event_side == side]


class _RaisingObserver:
    """An observer whose every method raises, after counting the call."""

    def __init__(self) -> None:
        self.calls = 0

    def read_started(self, side: Side) -> None:
        self.calls += 1
        msg = f"observer broke while starting {side}: {_SECRET_DETAIL}"
        raise RuntimeError(msg)

    def read_ended(self, side: Side, report: SideReadReport) -> None:
        self.calls += 1
        msg = f"observer broke while ending {side} ({report.succeeded}): {_SECRET_DETAIL}"
        raise RuntimeError(msg)


def _engine(
    source: Adapter,
    destination: Adapter,
    *,
    top_level: list[str],
    concurrent_load: bool = True,
) -> Potenda:
    return Potenda(
        source=source,
        destination=destination,
        config=None,  # ty: ignore[invalid-argument-type]
        top_level=top_level,
        concurrent_load=concurrent_load,
    )


# --- T002: what the observer receives ------------------------------------------------------


@pytest.mark.parametrize("concurrent_load", [True, False])
def test_a_successful_read_reports_its_start_then_its_end_with_counts(*, concurrent_load: bool) -> None:
    observer = _RecordingObserver()
    ptd = _engine(
        _FakeAdapter(devices=3, sites=2),
        _FakeAdapter(devices=1),
        top_level=["LocationSite", "IpamPrefix", "DcimDevice"],
        concurrent_load=concurrent_load,
    )
    ptd.read_observer = observer

    ptd.load_both_sides()

    source_events = observer.for_side("source")
    assert [name for name, _ in source_events] == ["started", "ended"]
    report = source_events[1][1]
    assert report is not None
    assert report == SideReadReport(
        succeeded=True,
        read_mode="full",
        nodes_by_kind={"DcimDevice": 3, "IpamPrefix": 0, "LocationSite": 2},
        error_class=None,
    )
    # Sorted by kind name, whatever the order of `top_level`.
    assert list(report.nodes_by_kind) == ["DcimDevice", "IpamPrefix", "LocationSite"]
    destination_events = observer.for_side("destination")
    assert [name for name, _ in destination_events] == ["started", "ended"]
    destination_report = destination_events[1][1]
    assert destination_report is not None
    assert destination_report.nodes_by_kind == {"DcimDevice": 1, "IpamPrefix": 0, "LocationSite": 0}


@pytest.mark.parametrize("concurrent_load", [True, False])
def test_a_failing_destination_reports_its_error_class_and_the_source_reports_its_own_end(
    *, concurrent_load: bool
) -> None:
    observer = _RecordingObserver()
    ptd = _engine(
        _FakeAdapter(devices=3, sites=2),
        _FakeAdapter(error=ConnectionError(_SECRET_DETAIL)),
        top_level=["DcimDevice", "LocationSite"],
        concurrent_load=concurrent_load,
    )
    ptd.read_observer = observer

    with pytest.raises(ValueError, match="An error occurred while loading"):
        ptd.load_both_sides()

    destination_events = observer.for_side("destination")
    assert [name for name, _ in destination_events] == ["started", "ended"]
    assert destination_events[1][1] == SideReadReport(
        succeeded=False, read_mode=None, nodes_by_kind={}, error_class="ConnectionError"
    )
    assert _SECRET_DETAIL not in repr(observer.events)
    source_events = observer.for_side("source")
    assert [name for name, _ in source_events] == ["started", "ended"]
    source_report = source_events[1][1]
    assert source_report is not None
    assert source_report.succeeded is True


def test_an_incremental_read_reports_its_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    observer = _RecordingObserver()
    ptd = _engine(_FakeAdapter(devices=2), _FakeAdapter(), top_level=["DcimDevice"], concurrent_load=False)
    ptd.read_observer = observer

    def incremental_load(*, side: str, adapter: Adapter) -> None:
        adapter.load()
        ptd._side_full_extract[side] = False

    monkeypatch.setattr(ptd, "load_one_side", incremental_load)

    ptd.load_both_sides()

    report = observer.for_side("source")[1][1]
    assert report is not None
    assert report.read_mode == "incremental"
    assert report.nodes_by_kind == {"DcimDevice": 2}


# --- T003: a failing observer, no observer, and the end log line ---------------------------


def test_a_raising_observer_does_not_change_the_read_and_logs_one_warning_per_call(
    caplog: pytest.LogCaptureFixture,
) -> None:
    source = _FakeAdapter(devices=3, sites=2)
    ptd = _engine(source, _FakeAdapter(), top_level=["DcimDevice", "LocationSite"], concurrent_load=False)
    observer = _RaisingObserver()
    ptd.read_observer = observer

    with caplog.at_level(logging.WARNING, logger="infrahub_sync.potenda"):
        ptd.load_both_sides()

    assert source.count("DcimDevice") == 3
    assert ptd._side_full_extract == {"A": True, "B": True}
    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    # Two sides, two calls each: every failed call logs exactly one warning.
    assert observer.calls == 4
    assert len(warnings) == 4
    assert all("RuntimeError" in record.getMessage() for record in warnings)
    assert _SECRET_DETAIL not in caplog.text


def test_a_raising_observer_does_not_hide_a_failed_read(caplog: pytest.LogCaptureFixture) -> None:
    ptd = _engine(
        _FakeAdapter(), _FakeAdapter(error=ConnectionError("down")), top_level=["DcimDevice"], concurrent_load=False
    )
    ptd.read_observer = _RaisingObserver()

    with caplog.at_level(logging.WARNING, logger="infrahub_sync.potenda"), pytest.raises(ValueError, match="down"):
        ptd.load_both_sides()


def test_without_an_observer_the_reads_behave_as_before() -> None:
    source = _FakeAdapter(devices=3, sites=2)
    destination = _FakeAdapter(devices=1)
    ptd = _engine(source, destination, top_level=["DcimDevice", "LocationSite"])

    assert ptd.read_observer is None
    ptd.load_both_sides()

    assert source.count("DcimDevice") == 3
    assert destination.count("DcimDevice") == 1
    assert ptd._side_full_extract == {"A": True, "B": True}


def test_each_read_end_logs_one_info_line_without_exception_text(caplog: pytest.LogCaptureFixture) -> None:
    ptd = _engine(
        _FakeAdapter(devices=3, sites=2),
        _FakeAdapter(error=ConnectionError(_SECRET_DETAIL)),
        top_level=["DcimDevice", "LocationSite"],
        concurrent_load=False,
    )

    with caplog.at_level(logging.INFO, logger="infrahub_sync.potenda"), pytest.raises(ValueError):
        ptd.load_both_sides()

    end_lines = [
        record.getMessage()
        for record in caplog.records
        if record.name == "infrahub_sync.potenda" and " read " in record.getMessage()
    ]
    assert end_lines == [
        "Load: source read succeeded (full): 5 nodes; DcimDevice 3, LocationSite 2",
        "Load: destination read failed (ConnectionError)",
    ]
    assert all(record.levelno == logging.INFO for record in caplog.records if record.getMessage() in end_lines)
    assert _SECRET_DETAIL not in caplog.text


class _UncountableAdapter(_FakeAdapter):
    """An adapter whose store cannot be counted, like a fake with no DiffSync store."""

    def count(self, model: object = None) -> int:
        msg = f"{type(self).__name__} has no store for {model}: {_SECRET_DETAIL}"
        raise AttributeError(msg)


def test_a_store_that_cannot_be_counted_does_not_fail_the_read(caplog: pytest.LogCaptureFixture) -> None:
    observer = _RecordingObserver()
    ptd = _engine(_UncountableAdapter(devices=2), _FakeAdapter(), top_level=["DcimDevice"], concurrent_load=False)
    ptd.read_observer = observer

    with caplog.at_level(logging.INFO, logger="infrahub_sync.potenda"):
        ptd.load_both_sides()

    assert observer.for_side("source")[1][1] == SideReadReport(succeeded=True, read_mode="full", nodes_by_kind={})
    warnings = [record.getMessage() for record in caplog.records if record.levelno == logging.WARNING]
    assert warnings == ["Load: could not count the source side's nodes (AttributeError)"]
    assert "Load: source read succeeded (full): 0 nodes" in caplog.text
    assert _SECRET_DETAIL not in caplog.text


@pytest.mark.parametrize("concurrent_load", [True, False])
def test_a_failing_side_raises_a_side_load_error_naming_its_role_and_adapter(*, concurrent_load: bool) -> None:
    """Spec 019 research R12: the read failure names the side; the error stays its cause."""
    cause = ConnectionError(_SECRET_DETAIL)
    ptd = _engine(
        _FakeAdapter(devices=1),
        _FakeAdapter(error=cause),
        top_level=["DcimDevice"],
        concurrent_load=concurrent_load,
    )

    with pytest.raises(SideLoadError) as excinfo:
        ptd.load_both_sides()

    assert isinstance(excinfo.value, ValueError)
    assert (excinfo.value.role, excinfo.value.adapter) == ("destination", "_FakeAdapter")
    assert excinfo.value.__cause__ is cause


def test_a_side_load_error_keeps_its_fields_when_pickled() -> None:
    error = SideLoadError("An error occurred while loading", role="source", adapter="netbox")

    copy = pickle.loads(pickle.dumps(error))  # noqa: S301 - the bytes come from this test

    assert (str(copy), copy.role, copy.adapter) == (str(error), "source", "netbox")
