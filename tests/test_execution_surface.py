"""Unit tests for the shared execution surface.

Part 1 — result schema, immutability, invariants, and secret redaction as raised
by the remote composition (DBA-010, SC-008).
Part 2 — validation refusals, tolerant configuration resolution, lock contention,
and the plan lifecycle (DBA-006/007, SC-004).
Part 3 — the confirmed serial-sync lifecycle and its idempotent second run
(DBA-005's automated analog, DBA-010's sync-side result schema; SC-003, SC-008).
"""

from __future__ import annotations

import dataclasses
import logging
import os
import subprocess  # noqa: S404 — patched, never invoked; the spy proves no subprocess starts
import traceback
from pathlib import Path
from typing import Any, NoReturn

import pytest
from filelock import Timeout

from infrahub_sync import SyncAdapter, SyncInstance, SyncStore
from infrahub_sync.cache.locks import pipeline_lock
from infrahub_sync.cache.parquet_io import write_plan
from infrahub_sync.cache.sidecars import RunFile
from infrahub_sync.execution import (
    RunExecutionError,
    RunResult,
    RunValidationError,
    collect_secret_values,
    execute_run,
    redact,
    resolve_sync_instance,
    run_remote_request,
)

SYNC_NAME = "canary-example"
RUN_ID = "20260731T1200-abcdef12"

# Deliberately unmistakable canaries so an assertion can only pass by real
# redaction, never by coincidence.
ENV_TOKEN_CANARY = "ZZ-ENV-INFRAHUB-TOKEN-CANARY-0001"  # noqa: S105 — a canary, not a credential
ENV_PATTERN_CANARY = "ZZ-ENV-NETBOX-TOKEN-CANARY-0002"
SOURCE_SETTING_CANARY = "ZZ-SETTINGS-SOURCE-CANARY-0003"
DEST_SETTING_CANARY = "ZZ-SETTINGS-DEST-CANARY-0004"
FILE_CONTENT_CANARY = "ZZ-FILE-CONTENT-CANARY-0005"
URL_USERINFO_CANARY = "ZZ-URL-USERINFO-CANARY-0006"

PWNED_MARKER = Path("/tmp/infrahub-sync-pwned-canary")  # noqa: S108 - never created; asserted absent


def _valid_config(name: str = SYNC_NAME) -> str:
    return f"""
name: {name}
source:
  name: mockdb
  settings:
    url: http://localhost:9999
    token: {SOURCE_SETTING_CANARY}
destination:
  name: infrahub
  settings:
    url: http://localhost:8000
    token: {DEST_SETTING_CANARY}
schema_mapping:
  - name: InfraDevice
    mapping: device
    identifiers: [name]
    fields:
      - name: name
        mapping: name
"""


@pytest.fixture
def config_dir(tmp_path: Path) -> str:
    """A configuration directory holding one valid `config.yml`."""
    target = tmp_path / "configs" / "b-good"
    target.mkdir(parents=True)
    (target / "config.yml").write_text(_valid_config(), encoding="utf-8")
    return str(tmp_path / "configs")


@pytest.fixture
def cache_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the cache (and therefore the pipeline lock) at the temp directory."""
    root = tmp_path / "cache"
    monkeypatch.setenv("INFRAHUB_SYNC_CACHE_DIR", str(root))
    return root / SYNC_NAME


@pytest.fixture
def seeded_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    """Seed both halves of the environment-credential collection rule."""
    monkeypatch.setenv("INFRAHUB_API_TOKEN", ENV_TOKEN_CANARY)
    monkeypatch.setenv("NETBOX_TOKEN", ENV_PATTERN_CANARY)


# --------------------------------------------------------------------------- #
# Fakes and spies
# --------------------------------------------------------------------------- #

PLAN_ROW_DEFAULTS = {
    "dest_id": None,
    "attribute": None,
    "old_value": None,
    "new_value": None,
    "owner": None,
    "skip_reason": None,
    "conflict_class": None,
}


def _plan_row(action: str, source_id: str) -> dict[str, Any]:
    return {"action": action, "resource": "InfraDevice", "source_id": source_id, **PLAN_ROW_DEFAULTS}


class _FakeDiff:
    def __init__(self, rows: list[dict[str, Any]]) -> None:  # ty: ignore[invalid-type-form]
        self.rows = rows

    def has_diffs(self) -> bool:
        return bool(self.rows)

    def str(self) -> str:  # ty: ignore[invalid-type-form]
        return f"fake-diff({len(self.rows)} rows)"


class _FakePotenda:
    """The engine surface `execute_run` actually touches — nothing more."""

    def __init__(
        self,
        *,
        run_dir: Path,
        rows: list[dict[str, Any]],
        factory_kwargs: dict[str, object],
        load_error: BaseException | None = None,
    ) -> None:
        self.run_dir = run_dir
        self.run_id = run_dir.name
        self.top_level = ["InfraDevice"]
        self.force_full_extract = False
        self.factory_kwargs = factory_kwargs
        self.rows = rows
        self.load_error = load_error
        self.loaded = False
        self.synced = False
        self.baseline_persisted = False
        self.guardrail_allow_drop: bool | None = None

    def load_both_sides(self) -> None:
        if self.load_error is not None:
            # The LOAD stage, distinct from the factory stage: potenda wraps every
            # load failure into `ValueError` too.
            raise self.load_error
        self.loaded = True

    def diff(self) -> _FakeDiff:
        return _FakeDiff(list(self.rows))

    def _diff_to_rows(self, diff: _FakeDiff) -> list[dict[str, Any]]:  # noqa: PLR6301 — mirrors Potenda's API
        return list(diff.rows)

    def write_plan(self, diff: _FakeDiff) -> None:
        write_plan(run_dir=self.run_dir, rows=self._diff_to_rows(diff))

    def check_rowcount_guardrail(self, *, allow_drop: bool) -> None:
        self.guardrail_allow_drop = allow_drop

    def sync(self, diff: _FakeDiff | None = None) -> None:  # noqa: ARG002 — keyword name is part of the API
        self.synced = True

    def persist_baseline_counts(self) -> None:
        self.baseline_persisted = True


class _SpyFactory:
    """Records every factory call; optionally raises instead of building an engine."""

    def __init__(
        self,
        *,
        cache_root: Path,
        rows: list[dict[str, Any]] | None = None,
        error: BaseException | None = None,
        load_error: BaseException | None = None,
    ) -> None:
        self.calls: list[dict[str, object]] = []
        self.cache_root = cache_root
        self.rows = rows if rows is not None else []
        self.error = error
        self.load_error = load_error
        self.engine: _FakePotenda | None = None

    def __call__(self, **kwargs: object) -> Any:  # noqa: ANN401 — a fake engine, not a real Potenda
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        run_dir = self.cache_root / RUN_ID
        run_dir.mkdir(parents=True, exist_ok=True)
        self.engine = _FakePotenda(
            run_dir=run_dir,
            rows=list(self.rows),
            factory_kwargs=kwargs,
            load_error=self.load_error,
        )
        return self.engine


class _ConvergingPotenda(_FakePotenda):
    """A fake engine whose destination CONVERGES when it is synced.

    `rows` is the shared pending-change list rather than a per-engine copy, and
    `sync` drains it — so a second run built by the same factory sees an empty
    diff. That is the fake analog of idempotent reconciliation: nothing about the
    surface is special-cased, the destination simply no longer differs.
    """

    def sync(self, diff: _FakeDiff | None = None) -> None:
        super().sync(diff)
        self.rows.clear()


class _ConvergingFactory:
    """Builds `_ConvergingPotenda` engines over ONE shared destination state.

    Each call gets its own run directory, as real run-id allocation does, so two
    sequential runs leave two distinguishable `run.json` files.
    """

    def __init__(self, *, cache_root: Path, rows: list[dict[str, Any]]) -> None:
        self.cache_root = cache_root
        self.pending = list(rows)
        self.calls: list[dict[str, object]] = []
        self.engines: list[_ConvergingPotenda] = []

    def __call__(self, **kwargs: object) -> Any:  # noqa: ANN401 — a fake engine, not a real Potenda
        self.calls.append(kwargs)
        run_dir = self.cache_root / f"20260731T120{len(self.calls)}-abcdef12"
        run_dir.mkdir(parents=True, exist_ok=True)
        engine = _ConvergingPotenda(run_dir=run_dir, rows=self.pending, factory_kwargs=kwargs)
        self.engines.append(engine)
        return engine


def _spy_reads(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """Record every filesystem read performed through `Path`."""
    reads: list[Path] = []
    real_read_text = Path.read_text
    real_open = Path.open

    def spy_read_text(self: Path, *args: Any, **kwargs: Any) -> str:  # noqa: ANN401 — passthrough wrapper
        reads.append(self)
        return real_read_text(self, *args, **kwargs)

    def spy_open(self: Path, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401 — passthrough wrapper
        reads.append(self)
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", spy_read_text)
    monkeypatch.setattr(Path, "open", spy_open)
    return reads


def _forbid_subprocesses(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args: object, **_kwargs: object) -> NoReturn:
        msg = f"a subprocess was started: {args!r}"
        raise AssertionError(msg)

    monkeypatch.setattr(subprocess, "Popen", boom)
    monkeypatch.setattr(os, "system", boom)


def _rendered_traceback(exc: BaseException) -> str:
    return "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))


# --------------------------------------------------------------------------- #
# Part 1 — RunResult schema, immutability, invariants
# --------------------------------------------------------------------------- #


def _result(**overrides: Any) -> RunResult:  # noqa: ANN401 — heterogeneous RunResult field values
    payload: dict[str, Any] = {
        "sync_name": SYNC_NAME,
        "operation": "plan",
        "run_id": RUN_ID,
        "status": "planned",
        "changed": True,
        "summary": {"create": 1, "update": 0, "delete": 0},
        "artifact_path": f"/tmp/cache/{SYNC_NAME}/{RUN_ID}",  # noqa: S108 - never touched on disk
    }
    payload.update(overrides)
    return RunResult(**payload)


def test_run_result_has_exactly_seven_fields() -> None:
    names = [f.name for f in dataclasses.fields(RunResult)]
    assert len(names) == 7
    assert names == ["sync_name", "operation", "run_id", "status", "changed", "summary", "artifact_path"]


def test_run_result_uses_slots_and_rejects_extra_attributes() -> None:
    result = _result()
    assert not hasattr(result, "__dict__")
    with pytest.raises(AttributeError):
        object.__setattr__(result, "extra", 1)  # noqa: PLC2801 — the only way past frozen __setattr__


def test_run_result_fields_cannot_be_reassigned() -> None:
    result = _result()
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.status = "no-change"  # ty: ignore[invalid-assignment]


def test_run_result_summary_is_read_only_after_validation() -> None:
    """`frozen=True` stops rebinding; the mappingproxy stops mutation."""
    result = _result()
    with pytest.raises(TypeError):
        result.summary["create"] += 1


def test_run_result_summary_is_decoupled_from_the_caller_dict() -> None:
    original = {"create": 1, "update": 0, "delete": 0}
    result = _result(summary=original)
    original["create"] = 99
    assert result.summary["create"] == 1


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({}, id="plan-with-changes"),
        pytest.param(
            {"operation": "sync", "status": "applied", "changed": True},
            id="sync-applied",
        ),
        pytest.param(
            {"status": "no-change", "changed": False, "summary": {"create": 0, "update": 0, "delete": 0}},
            id="plan-no-change",
        ),
        pytest.param(
            {
                "operation": "sync",
                "status": "no-change",
                "changed": False,
                "summary": {"create": 0, "update": 0, "delete": 0},
            },
            id="sync-no-change",
        ),
    ],
)
def test_run_result_accepts_consistent_combinations(overrides: dict[str, Any]) -> None:
    assert isinstance(_result(**overrides), RunResult)


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"changed": False}, id="changed-false-with-nonzero-summary"),
        pytest.param(
            {"status": "no-change", "changed": True},
            id="no-change-status-with-changed-true",
        ),
        pytest.param(
            {"changed": True, "summary": {"create": 0, "update": 0, "delete": 0}}, id="changed-true-with-zero-summary"
        ),
        pytest.param({"operation": "sync"}, id="planned-status-on-sync-operation"),
        pytest.param({"status": "applied"}, id="applied-status-on-plan-operation"),
        pytest.param({"run_id": "not-the-last-segment"}, id="run-id-artifact-path-mismatch"),
        pytest.param({"summary": {"create": 1}}, id="summary-missing-keys"),
        pytest.param(
            {"summary": {"create": 1, "update": 0, "delete": 0, "skip": 0}},
            id="summary-extra-key",
        ),
    ],
)
def test_run_result_rejects_invariant_violations(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        _result(**overrides)


# --------------------------------------------------------------------------- #
# Part 1 — secret redaction at the remote boundary
# --------------------------------------------------------------------------- #


@pytest.mark.usefixtures("seeded_secrets")
def test_validation_error_redacts_env_secret_values(tmp_path: Path) -> None:
    """A refusal that echoes the requested name must not echo a secret value."""
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(RunValidationError) as excinfo:
        run_remote_request(ENV_TOKEN_CANARY, config_directory=str(empty))
    message = str(excinfo.value)
    assert ENV_TOKEN_CANARY not in message
    assert "***" in message


@pytest.mark.usefixtures("seeded_secrets")
def test_execution_error_redacts_the_whole_cause_chain(config_dir: str, cache_root: Path) -> None:
    """No traceback rendering of the wrapped error may show an unredacted message."""
    inner = ConnectionError(f"upstream rejected token {ENV_PATTERN_CANARY}")
    outer = RuntimeError(f"engine blew up using {ENV_TOKEN_CANARY} / {SOURCE_SETTING_CANARY}")
    outer.__cause__ = inner
    factory = _SpyFactory(cache_root=cache_root, error=outer)

    with pytest.raises(RunExecutionError) as excinfo:
        run_remote_request(SYNC_NAME, config_directory=config_dir, _potenda_factory=factory)

    rendered = _rendered_traceback(excinfo.value)
    for canary in (ENV_TOKEN_CANARY, ENV_PATTERN_CANARY, SOURCE_SETTING_CANARY, DEST_SETTING_CANARY):
        assert canary not in str(excinfo.value)
        assert canary not in rendered
    assert "***" in str(excinfo.value)
    assert "***" in rendered
    assert SYNC_NAME in str(excinfo.value)


# --------------------------------------------------------------------------- #
# Part 1 — the secret-value collection set
# --------------------------------------------------------------------------- #


def _instance(
    *,
    source_settings: dict[str, Any] | None = None,
    destination_settings: dict[str, Any] | None = None,
    store: SyncStore | None = None,
) -> SyncInstance:
    """A resolved instance carrying arbitrary adapter/store settings."""
    return SyncInstance(
        name=SYNC_NAME,
        source=SyncAdapter(name="netbox", settings=source_settings or {}),
        destination=SyncAdapter(name="infrahub", settings=destination_settings or {}),
        store=store,
        directory="/nonexistent",
    )


@pytest.mark.parametrize(
    ("kwargs", "environ", "expected"),
    [
        pytest.param(
            {"source_settings": {"url": f"http://admin:{URL_USERINFO_CANARY}@netbox.local/api"}},
            {},
            URL_USERINFO_CANARY,
            id="password-inside-a-url",
        ),
        pytest.param({}, {"TOKEN": URL_USERINFO_CANARY}, URL_USERINFO_CANARY, id="bare-TOKEN-env-name"),
        pytest.param({}, {"PASSWORD": URL_USERINFO_CANARY}, URL_USERINFO_CANARY, id="bare-PASSWORD-env-name"),
        pytest.param(
            {},
            {"AWS_SECRET_ACCESS_KEY": URL_USERINFO_CANARY},
            URL_USERINFO_CANARY,
            id="aws-secret-access-key",
        ),
        pytest.param(
            {"source_settings": {"auth": URL_USERINFO_CANARY}},
            {},
            URL_USERINFO_CANARY,
            id="ipfabric-auth-key",
        ),
        pytest.param(
            {"source_settings": {"token": 12345678901234}},
            {},
            "12345678901234",
            id="non-string-value",
        ),
        pytest.param(
            {"source_settings": {"headers": {"Authorization": f"Bearer {URL_USERINFO_CANARY}"}}},
            {},
            f"Bearer {URL_USERINFO_CANARY}",
            id="nested-headers",
        ),
        pytest.param(
            {"source_settings": {"params": {"api_key": URL_USERINFO_CANARY}}},
            {},
            URL_USERINFO_CANARY,
            id="nested-params",
        ),
        pytest.param(
            {"store": SyncStore(type="redis", settings={"password": URL_USERINFO_CANARY})},
            {},
            URL_USERINFO_CANARY,
            id="store-settings-password",
        ),
        pytest.param(
            {"source_settings": {"token_env_vars": ["MY_ADAPTER_PASSPHRASE"]}},
            {"MY_ADAPTER_PASSPHRASE": URL_USERINFO_CANARY},
            URL_USERINFO_CANARY,
            id="config-named-env-var-list",
        ),
    ],
)
def test_collected_secret_values_cover_realistic_credential_placements(
    kwargs: dict[str, Any],
    environ: dict[str, str],
    expected: str,
) -> None:
    assert expected in collect_secret_values(_instance(**kwargs), environ=environ)


@pytest.mark.parametrize(
    ("kwargs", "environ", "unwanted"),
    [
        pytest.param({}, {"SKIP_TOKEN": "2"}, "2", id="value-too-short-to-redact"),
        pytest.param({}, {"LC_ALL": "en_US.UTF-8"}, "en_US.UTF-8", id="unrelated-env-name"),
        pytest.param({}, {"SSH_AUTH_SOCK": "/private/tmp/socket"}, "/private/tmp/socket", id="auth-mid-name-env"),
        pytest.param(
            {"source_settings": {"url_env_vars": ["NETBOX_ADDRESS"]}},
            {"NETBOX_ADDRESS": "http://netbox.local"},
            "NETBOX_ADDRESS",
            id="non-secret-env-var-list-names",
        ),
        pytest.param(
            {"source_settings": {"url": "http://netbox.local/api"}},
            {},
            "http://netbox.local/api",
            id="url-without-userinfo",
        ),
        pytest.param({"source_settings": {"password": None}}, {}, "None", id="none-value"),
        pytest.param({"source_settings": {"verify_ssl": False}}, {}, "False", id="bool-value"),
    ],
)
def test_collected_secret_values_exclude_values_that_would_shred_messages(
    kwargs: dict[str, Any],
    environ: dict[str, str],
    unwanted: str,
) -> None:
    assert unwanted not in collect_secret_values(_instance(**kwargs), environ=environ)


def test_a_short_secret_value_does_not_shred_unrelated_message_text(
    config_dir: str,
    cache_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """L2: `SKIP_TOKEN=2` must not turn the lock message into "within 0.*** seconds"."""
    monkeypatch.setenv("SKIP_TOKEN", "2")
    factory = _SpyFactory(cache_root=cache_root)

    with pipeline_lock(SYNC_NAME, timeout=5), pytest.raises(RunExecutionError) as excinfo:
        run_remote_request(
            SYNC_NAME,
            config_directory=config_dir,
            _potenda_factory=factory,
            _lock_timeout=0.2,
        )

    assert "within 0.2 seconds" in str(excinfo.value)


def test_url_userinfo_is_redacted_from_a_wrapped_execution_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A password in `settings.url` is redacted like a sibling `token` value is."""
    root = tmp_path / "configs" / "userinfo"
    root.mkdir(parents=True)
    endpoint = f"http://admin:{URL_USERINFO_CANARY}@localhost:9999/api"
    (root / "config.yml").write_text(
        _valid_config().replace("url: http://localhost:9999", f"url: {endpoint}"),
        encoding="utf-8",
    )
    monkeypatch.setenv("INFRAHUB_SYNC_CACHE_DIR", str(tmp_path / "cache"))
    factory = _SpyFactory(
        cache_root=tmp_path / "cache" / SYNC_NAME,
        error=RuntimeError(f"connection to {endpoint} refused"),
    )

    with pytest.raises(RunExecutionError) as excinfo:
        run_remote_request(SYNC_NAME, config_directory=str(tmp_path / "configs"), _potenda_factory=factory)

    assert URL_USERINFO_CANARY not in str(excinfo.value)
    assert URL_USERINFO_CANARY not in _rendered_traceback(excinfo.value)
    assert "***" in str(excinfo.value)


def test_redact_leaves_a_message_untouched_when_nothing_was_collected() -> None:
    assert redact("within 60.0 seconds", collect_secret_values(_instance(), environ={"SKIP_TOKEN": "1"})) == (
        "within 60.0 seconds"
    )


# --------------------------------------------------------------------------- #
# Part 2 — sync_name refusals (SC-004)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "sync_name",
    [
        pytest.param("nope", id="unknown"),
        pytest.param("../custom-example", id="parent-traversal"),
        pytest.param("/etc/passwd", id="absolute-path"),
        pytest.param("a/b", id="path-separator"),
        pytest.param("--help", id="flag-like"),
        pytest.param(f"$(touch {PWNED_MARKER})", id="command-substitution"),
    ],
)
def test_negative_sync_names_are_refused_without_reading_out_or_spawning(
    sync_name: str,
    config_dir: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reads = _spy_reads(monkeypatch)
    _forbid_subprocesses(monkeypatch)

    with pytest.raises(RunValidationError) as excinfo:
        run_remote_request(sync_name, config_directory=config_dir)

    assert sync_name in str(excinfo.value)
    # The spy must have observed the in-directory walk, or "no read outside"
    # would be vacuously true.
    assert reads, "the filesystem spy recorded nothing — the walk did not run"
    outside = [p for p in reads if not p.is_relative_to(Path(config_dir))]
    assert outside == []
    assert not PWNED_MARKER.exists()


def test_matched_but_invalid_configuration_names_only_the_name_and_path(tmp_path: Path) -> None:
    """Rule (a): the name IS determinable, so this file is the matched one.

    The invalid value is a *string* where `SyncAdapter` is expected, because
    pydantic echoes that value verbatim as `input_value=...` — so chaining the
    parse detail would leak this file's contents. Verified: the raw
    `SyncConfig(**data)` message for this input DOES contain the canary.
    """
    target = tmp_path / "configs" / "broken"
    target.mkdir(parents=True)
    config_file = target / "config.yml"
    config_file.write_text(
        f"name: {SYNC_NAME}\nsource: {FILE_CONTENT_CANARY}\ndestination:\n  name: infrahub\n",
        encoding="utf-8",
    )

    with pytest.raises(RunValidationError) as excinfo:
        run_remote_request(SYNC_NAME, config_directory=str(tmp_path / "configs"))

    message = str(excinfo.value)
    assert SYNC_NAME in message
    assert str(config_file) in message
    # The pydantic detail (which echoes `input_value=...`) is never chained.
    assert excinfo.value.__cause__ is None
    assert FILE_CONTENT_CANARY not in message
    assert FILE_CONTENT_CANARY not in _rendered_traceback(excinfo.value)


def test_matched_but_invalid_configuration_handles_non_pydantic_failures(tmp_path: Path) -> None:
    """`SyncConfig`'s own validator raises `TypeError`, not `ValidationError`."""
    target = tmp_path / "configs" / "broken"
    target.mkdir(parents=True)
    config_file = target / "config.yml"
    config_file.write_text(
        f"name: {SYNC_NAME}\nsource:\n  name: mockdb\ndestination:\n  name: infrahub\ndiffsync_flags: {FILE_CONTENT_CANARY}\n",
        encoding="utf-8",
    )

    with pytest.raises(RunValidationError) as excinfo:
        run_remote_request(SYNC_NAME, config_directory=str(tmp_path / "configs"))

    assert str(config_file) in str(excinfo.value)
    assert excinfo.value.__cause__ is None


def test_unparseable_configuration_is_skipped_counted_and_never_matched(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Rule (c): no determinable name, so it can never be the matched file."""
    target = tmp_path / "configs" / "broken"
    target.mkdir(parents=True)
    config_file = target / "config.yml"
    config_file.write_text(f"name: [unclosed\nsecret: {FILE_CONTENT_CANARY}\n", encoding="utf-8")

    with caplog.at_level(logging.DEBUG, logger="infrahub_sync.execution"), pytest.raises(RunValidationError) as excinfo:
        run_remote_request(SYNC_NAME, config_directory=str(tmp_path / "configs"))

    message = str(excinfo.value)
    assert SYNC_NAME in message
    assert "1 file(s) could not be read" in message
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert str(config_file) in warnings[0].getMessage()
    assert FILE_CONTENT_CANARY not in caplog.text
    assert "unclosed" not in caplog.text
    assert FILE_CONTENT_CANARY not in message


def test_broken_neighbour_does_not_block_another_name(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The tolerant walk resolves the requested name past unrelated bad files."""
    root = tmp_path / "configs"
    broken = root / "a-broken"
    broken.mkdir(parents=True)
    broken_file = broken / "config.yml"
    broken_file.write_text(f"name: [unclosed\nsecret: {FILE_CONTENT_CANARY}\n", encoding="utf-8")
    good = root / "b-good"
    good.mkdir()
    (good / "config.yml").write_text(_valid_config(), encoding="utf-8")
    other_invalid = root / "c-other-invalid"
    other_invalid.mkdir()
    (other_invalid / "config.yml").write_text("name: some-other-sync\n", encoding="utf-8")

    with caplog.at_level(logging.DEBUG, logger="infrahub_sync.execution"):
        instance = resolve_sync_instance(SYNC_NAME, directory=str(root))

    assert instance.name == SYNC_NAME
    # `directory` is the matched config.yml's own parent, not the configured root,
    # so `utils.import_adapter` still finds the generated adapter next to it.
    assert instance.directory == str(good)
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings == [f"Skipping sync configuration {broken_file}: it could not be read"]


def _write_non_utf8_neighbour(root: Path) -> Path:
    """Write a latin-1 `config.yml` — `read_text(encoding="utf-8")` cannot decode it."""
    broken = root / "a-not-utf8"
    broken.mkdir(parents=True)
    broken_file = broken / "config.yml"
    broken_file.write_bytes(f"name: caf\xe9\nsecret: {FILE_CONTENT_CANARY}\n".encode("latin-1"))
    return broken_file


def test_non_utf8_neighbour_is_skipped_with_a_path_only_warning(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """`UnicodeDecodeError` is a `ValueError`, so it is caught by name in the walk.

    Without it in the per-file catch, a single non-UTF-8 file made every OTHER
    name in the directory unresolvable.
    """
    root = tmp_path / "configs"
    broken_file = _write_non_utf8_neighbour(root)
    good = root / "b-good"
    good.mkdir()
    (good / "config.yml").write_text(_valid_config(), encoding="utf-8")

    with caplog.at_level(logging.DEBUG, logger="infrahub_sync.execution"):
        instance = resolve_sync_instance(SYNC_NAME, directory=str(root))

    assert instance.name == SYNC_NAME
    assert instance.directory == str(good)
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings == [f"Skipping sync configuration {broken_file}: it could not be read"]
    assert FILE_CONTENT_CANARY not in caplog.text


def test_non_utf8_neighbour_does_not_break_a_remote_plan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reviewer's reproduction: a latin-1 neighbour raised a raw `UnicodeDecodeError`."""
    root = tmp_path / "configs"
    _write_non_utf8_neighbour(root)
    good = root / "b-good"
    good.mkdir()
    (good / "config.yml").write_text(_valid_config(), encoding="utf-8")
    monkeypatch.setenv("INFRAHUB_SYNC_CACHE_DIR", str(tmp_path / "cache"))
    factory = _SpyFactory(cache_root=tmp_path / "cache" / SYNC_NAME, rows=[_plan_row("create", "dev01")])

    result = run_remote_request(SYNC_NAME, config_directory=str(root), _potenda_factory=factory)

    assert result.status == "planned"
    assert len(factory.calls) == 1


@pytest.mark.usefixtures("seeded_secrets")
def test_a_resolution_failure_that_is_not_a_refusal_is_wrapped_and_sanitized(
    config_dir: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`resolve_sync_instance` runs INSIDE the sanitize-and-wrap boundary."""

    fault = UnicodeDecodeError("utf-8", b"\xe9", 0, 1, f"invalid start byte near {ENV_TOKEN_CANARY}")

    def boom(*_args: object, **_kwargs: object) -> NoReturn:
        raise fault

    monkeypatch.setattr("infrahub_sync.execution.resolve_sync_instance", boom)

    with pytest.raises(RunExecutionError) as excinfo:
        run_remote_request(SYNC_NAME, config_directory=config_dir)

    message = str(excinfo.value)
    assert "UnicodeDecodeError" in message
    assert ENV_TOKEN_CANARY not in message
    assert ENV_TOKEN_CANARY not in _rendered_traceback(excinfo.value)


# --------------------------------------------------------------------------- #
# Part 2 — refusals before adapter construction
# --------------------------------------------------------------------------- #


def test_unconfirmed_sync_is_refused_before_the_engine_is_built(config_dir: str, cache_root: Path) -> None:
    instance = resolve_sync_instance(SYNC_NAME, directory=config_dir)
    factory = _SpyFactory(cache_root=cache_root)

    with pytest.raises(RunValidationError) as excinfo:
        execute_run(instance, operation="sync", potenda_factory=factory)

    assert "confirm_writes=true is required to run operation=sync" in str(excinfo.value)
    assert factory.calls == []
    assert not cache_root.exists()


def test_unconfirmed_sync_is_refused_through_the_remote_composition(config_dir: str, cache_root: Path) -> None:
    factory = _SpyFactory(cache_root=cache_root)
    with pytest.raises(RunValidationError):
        run_remote_request(SYNC_NAME, operation="sync", config_directory=config_dir, _potenda_factory=factory)
    assert factory.calls == []


def test_unknown_operation_is_refused(config_dir: str, cache_root: Path) -> None:
    instance = resolve_sync_instance(SYNC_NAME, directory=config_dir)
    factory = _SpyFactory(cache_root=cache_root)
    with pytest.raises(RunValidationError):
        execute_run(instance, operation="apply", potenda_factory=factory)  # ty: ignore[invalid-argument-type]
    assert factory.calls == []


# --------------------------------------------------------------------------- #
# Part 2 — pipeline-lock contention (bounded, via the sanctioned seam)
# --------------------------------------------------------------------------- #


def test_execute_run_lets_lock_timeout_propagate_unchanged(config_dir: str, cache_root: Path) -> None:
    instance = resolve_sync_instance(SYNC_NAME, directory=config_dir)
    factory = _SpyFactory(cache_root=cache_root)
    with pipeline_lock(SYNC_NAME, timeout=5), pytest.raises(Timeout):
        execute_run(instance, operation="plan", potenda_factory=factory, _lock_timeout=0.2)
    assert factory.calls == []


def test_run_remote_request_wraps_lock_timeout(config_dir: str, cache_root: Path) -> None:
    factory = _SpyFactory(cache_root=cache_root)
    with pipeline_lock(SYNC_NAME, timeout=5), pytest.raises(RunExecutionError) as excinfo:
        run_remote_request(
            SYNC_NAME,
            config_directory=config_dir,
            _potenda_factory=factory,
            _lock_timeout=0.2,
        )
    message = str(excinfo.value)
    assert SYNC_NAME in message
    assert "0.2" in message
    assert factory.calls == []


# --------------------------------------------------------------------------- #
# Part 2 — engine failures
# --------------------------------------------------------------------------- #


def test_factory_value_error_propagates_from_execute_run(config_dir: str, cache_root: Path) -> None:
    """`execute_run` never wraps: the ORIGINAL type reaches the caller."""
    instance = resolve_sync_instance(SYNC_NAME, directory=config_dir)
    factory = _SpyFactory(
        cache_root=cache_root,
        error=ValueError("Error initializing InfrahubAdapter: Both url and token must be specified!"),
    )
    with pytest.raises(ValueError, match="Both url and token must be specified"):
        execute_run(instance, operation="plan", potenda_factory=factory)


@pytest.mark.usefixtures("seeded_secrets")
def test_remote_missing_credential_names_the_environment_variables(config_dir: str, cache_root: Path) -> None:
    factory = _SpyFactory(
        cache_root=cache_root,
        error=ValueError("Error initializing InfrahubAdapter: Both url and token must be specified!"),
    )
    with pytest.raises(RunExecutionError) as excinfo:
        run_remote_request(SYNC_NAME, config_directory=config_dir, _potenda_factory=factory)

    message = str(excinfo.value)
    assert "Failed to initialize the Sync Instance" in message
    assert "INFRAHUB_ADDRESS" in message
    assert "INFRAHUB_API_TOKEN" in message
    assert ENV_TOKEN_CANARY not in message
    assert ENV_TOKEN_CANARY not in _rendered_traceback(excinfo.value)


def test_factory_stage_value_error_keeps_the_initialization_wording(config_dir: str, cache_root: Path) -> None:
    """Only the FACTORY stage gets today's CLI "Failed to initialize" wording."""
    factory = _SpyFactory(
        cache_root=cache_root,
        error=ValueError("Error initializing MockdbAdapter: settings are incomplete"),
    )
    with pytest.raises(RunExecutionError) as excinfo:
        run_remote_request(SYNC_NAME, config_directory=config_dir, _potenda_factory=factory)

    message = str(excinfo.value)
    assert message.startswith("Failed to initialize the Sync Instance: ")
    assert "Error initializing MockdbAdapter" in message
    # `mockdb` is not a known adapter, so no environment variables are named.
    assert "Set the runner-environment variables" not in message


def test_load_stage_value_error_is_labeled_by_stage_not_by_initialization(
    config_dir: str,
    cache_root: Path,
) -> None:
    """`potenda` wraps every load failure into `ValueError` — the spec's named fault.

    Reporting it as an initialization failure told an operator with an unreachable
    destination to check their credentials.
    """
    factory = _SpyFactory(
        cache_root=cache_root,
        load_error=ValueError("An error occurred while loading Infrahub: destination is unreachable"),
    )
    with pytest.raises(RunExecutionError) as excinfo:
        run_remote_request(SYNC_NAME, config_directory=config_dir, _potenda_factory=factory)

    message = str(excinfo.value)
    assert "Failed to initialize the Sync Instance" not in message
    assert f"Sync {SYNC_NAME!r} failed during operation=plan" in message
    assert "ValueError: An error occurred while loading Infrahub: destination is unreachable" in message
    assert "Set the runner-environment variables" not in message
    assert len(factory.calls) == 1


def test_a_result_invariant_violation_is_not_reported_as_a_credential_problem(
    config_dir: str,
    cache_root: Path,
) -> None:
    """`RunResult.__post_init__` raises `ValueError` for an internal invariant bug.

    Injected here as the invariant message the dataclass itself would raise, so an
    internal bug reads as one instead of as a missing credential.
    """
    factory = _SpyFactory(
        cache_root=cache_root,
        load_error=ValueError("summary must carry exactly the keys ('create', 'update', 'delete')"),
    )
    with pytest.raises(RunExecutionError) as excinfo:
        run_remote_request(SYNC_NAME, config_directory=config_dir, _potenda_factory=factory)

    message = str(excinfo.value)
    assert "Failed to initialize the Sync Instance" not in message
    assert "summary must carry exactly the keys" in message


@pytest.mark.parametrize(
    ("adapter_error", "expected_env_vars"),
    [
        pytest.param(
            "Error initializing PrometheusAdapter: Prometheus 'url' must be specified!",
            ("PROM_URL", "PROM_TOKEN"),
            id="prometheus-url",
        ),
        pytest.param(
            "Error initializing NetboxAdapter: Both url and token must be specified!",
            ("NETBOX_ADDRESS", "NETBOX_TOKEN"),
            id="netbox-url-and-token",
        ),
        pytest.param(
            "Error initializing IpfabricsyncAdapter: Both url and auth must be specified!",
            ("IPF_URL", "IPF_TOKEN"),
            id="ipfabric-url-and-auth",
        ),
        pytest.param(
            "Error initializing PeeringmanagerAdapter: Authentication method 'token' requires a valid API token!",
            ("PEERING_MANAGER_ADDRESS", "PEERING_MANAGER_TOKEN"),
            id="generic-rest-api-wording",
        ),
    ],
)
def test_missing_credential_hint_names_the_failing_adapters_variables(
    adapter_error: str,
    expected_env_vars: tuple[str, ...],
    config_dir: str,
    cache_root: Path,
) -> None:
    """Attribution follows the FAILING adapter, not whichever side mentions Infrahub."""
    factory = _SpyFactory(cache_root=cache_root, error=ValueError(adapter_error))
    with pytest.raises(RunExecutionError) as excinfo:
        run_remote_request(SYNC_NAME, config_directory=config_dir, _potenda_factory=factory)

    message = str(excinfo.value)
    for env_var in expected_env_vars:
        assert env_var in message
    # The configuration's destination IS infrahub, which is exactly what used to
    # make every adapter's missing input read as an Infrahub credential problem.
    assert "INFRAHUB_ADDRESS" not in message


@pytest.mark.parametrize(
    "adapter_error",
    [
        pytest.param("Error initializing MockdbAdapter: url must be specified!", id="unknown-adapter"),
        pytest.param("Failed to build the engine: url must be specified!", id="no-adapter-prefix"),
        pytest.param("Error initializing NetboxAdapter: the API returned 500", id="not-a-credential-refusal"),
    ],
)
def test_missing_credential_hint_stays_silent_when_it_cannot_attribute(
    adapter_error: str,
    config_dir: str,
    cache_root: Path,
) -> None:
    factory = _SpyFactory(cache_root=cache_root, error=ValueError(adapter_error))
    with pytest.raises(RunExecutionError) as excinfo:
        run_remote_request(SYNC_NAME, config_directory=config_dir, _potenda_factory=factory)

    assert "Set the runner-environment variables" not in str(excinfo.value)


def test_remote_import_error_is_wrapped(config_dir: str, cache_root: Path) -> None:
    factory = _SpyFactory(cache_root=cache_root, error=ImportError("Could not load the following adapter(s): mockdb"))
    with pytest.raises(RunExecutionError) as excinfo:
        run_remote_request(SYNC_NAME, config_directory=config_dir, _potenda_factory=factory)
    assert "adapter" in str(excinfo.value)
    assert SYNC_NAME in str(excinfo.value)


# --------------------------------------------------------------------------- #
# Part 2 — option passthrough and the plan lifecycle
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("branch", [None, "feature-x"])
def test_branch_reaches_the_factory_from_execute_run(
    config_dir: str,
    cache_root: Path,
    branch: str | None,
) -> None:
    instance = resolve_sync_instance(SYNC_NAME, directory=config_dir)
    factory = _SpyFactory(cache_root=cache_root)
    kwargs: dict[str, Any] = {"branch": branch} if branch is not None else {}
    execute_run(instance, operation="plan", potenda_factory=factory, **kwargs)
    assert factory.calls[0]["branch"] == branch


@pytest.mark.parametrize("branch", [None, "feature-x"])
def test_branch_reaches_the_factory_from_run_remote_request(
    config_dir: str,
    cache_root: Path,
    branch: str | None,
) -> None:
    factory = _SpyFactory(cache_root=cache_root)
    run_remote_request(SYNC_NAME, "plan", False, branch, config_directory=config_dir, _potenda_factory=factory)  # noqa: FBT003
    call = factory.calls[0]
    assert call["branch"] == branch
    # Remote runs pin the CLI engine defaults, with progress display disabled.
    assert call["show_progress"] is False
    assert call["concurrent_load"] is True
    assert call["continue_on_error"] is False
    assert call["run_id"] is None
    assert call["verbosity"] == logging.INFO


def test_successful_plan_writes_the_diff_lifecycle(config_dir: str, cache_root: Path) -> None:
    instance = resolve_sync_instance(SYNC_NAME, directory=config_dir)
    rows = [_plan_row("create", "core01"), _plan_row("create", "core02"), _plan_row("update", "edge01")]
    factory = _SpyFactory(cache_root=cache_root, rows=rows)

    result = execute_run(instance, operation="plan", potenda_factory=factory)

    run_dir = cache_root / RUN_ID
    assert result == RunResult(
        sync_name=SYNC_NAME,
        operation="plan",
        run_id=RUN_ID,
        status="planned",
        changed=True,
        summary={"create": 2, "update": 1, "delete": 0},
        artifact_path=str(run_dir),
    )
    assert (run_dir / "plan.parquet").exists()
    run_file = RunFile.load_or_default(run_dir / "run.json")
    assert run_file.mode == "diff"
    assert run_file.status == "dry-run"
    assert run_file.summary == {"resources": 1}
    assert run_file.finished_at is not None
    assert factory.engine is not None
    assert factory.engine.force_full_extract is True
    assert factory.engine.loaded is True
    assert factory.engine.synced is False


def test_empty_plan_reports_no_change(config_dir: str, cache_root: Path) -> None:
    instance = resolve_sync_instance(SYNC_NAME, directory=config_dir)
    factory = _SpyFactory(cache_root=cache_root, rows=[])

    result = execute_run(instance, operation="plan", potenda_factory=factory)

    assert result.status == "no-change"
    assert result.changed is False
    assert dict(result.summary) == {"create": 0, "update": 0, "delete": 0}
    assert RunFile.load_or_default(cache_root / RUN_ID / "run.json").status == "dry-run"


def test_plan_through_the_remote_composition_returns_a_result(config_dir: str, cache_root: Path) -> None:
    factory = _SpyFactory(cache_root=cache_root, rows=[_plan_row("create", "core01")])
    result = run_remote_request(SYNC_NAME, config_directory=config_dir, _potenda_factory=factory)
    assert result.status == "planned"
    assert result.run_id == RUN_ID
    assert Path(result.artifact_path).name == result.run_id


def test_lifecycle_failure_marks_run_json_failed_and_reraises(config_dir: str, cache_root: Path) -> None:
    """The preserved CLI pattern: run.json is never left at status='running'."""
    instance = resolve_sync_instance(SYNC_NAME, directory=config_dir)
    factory = _SpyFactory(cache_root=cache_root)

    def exploding_load() -> None:
        msg = "diff engine unavailable"
        raise RuntimeError(msg)

    original_call = factory.__call__

    def patched(**kwargs: object) -> Any:  # noqa: ANN401 — a fake engine, not a real Potenda
        engine = original_call(**kwargs)
        engine.load_both_sides = exploding_load
        return engine

    with pytest.raises(RuntimeError, match="diff engine unavailable"):
        execute_run(instance, operation="plan", potenda_factory=patched)

    assert RunFile.load_or_default(cache_root / RUN_ID / "run.json").status == "failed"


# --------------------------------------------------------------------------- #
# Part 3 — the confirmed serial-sync lifecycle (DBA-005's automated analog)
# --------------------------------------------------------------------------- #

# The five devices of the qualified `custom_adapter` fixture, so the unit analog
# and the live DBA-005 verification describe the same shape of change.
FIXTURE_DEVICES = ("core01", "core02", "core03", "edge01", "edge02")
TIMING_LOG_PREFIX = "Sync: Completed in"
NO_DIFF_LOG = "No difference found. Nothing to sync"


def _fixture_creates() -> list[dict[str, Any]]:
    return [_plan_row("create", name) for name in FIXTURE_DEVICES]


def test_confirmed_sync_applies_and_writes_the_serial_lifecycle(config_dir: str, cache_root: Path) -> None:
    """`operation="sync"` + `confirm_writes=True` applies the plan and reports it."""
    instance = resolve_sync_instance(SYNC_NAME, directory=config_dir)
    factory = _SpyFactory(cache_root=cache_root, rows=_fixture_creates())

    result = execute_run(instance, operation="sync", confirm_writes=True, potenda_factory=factory)

    run_dir = cache_root / RUN_ID
    assert result == RunResult(
        sync_name=SYNC_NAME,
        operation="sync",
        run_id=RUN_ID,
        status="applied",
        changed=True,
        summary={"create": 5, "update": 0, "delete": 0},
        artifact_path=str(run_dir),
    )
    assert (run_dir / "plan.parquet").exists()
    run_file = RunFile.load_or_default(run_dir / "run.json")
    assert run_file.mode == "sync"
    assert run_file.status == "applied"
    assert run_file.summary == {"resources": 1, "mode": "serial"}
    assert run_file.finished_at is not None
    engine = factory.engine
    assert engine is not None
    assert engine.force_full_extract is True
    assert engine.loaded is True
    assert engine.synced is True
    assert engine.baseline_persisted is True
    assert engine.guardrail_allow_drop is False


def test_confirmed_sync_summary_counts_every_action(config_dir: str, cache_root: Path) -> None:
    """The per-action summary is derived from the in-memory plan rows."""
    instance = resolve_sync_instance(SYNC_NAME, directory=config_dir)
    rows = [
        _plan_row("create", "core01"),
        _plan_row("create", "core02"),
        _plan_row("update", "edge01"),
        _plan_row("delete", "edge02"),
    ]
    factory = _SpyFactory(cache_root=cache_root, rows=rows)

    result = execute_run(instance, operation="sync", confirm_writes=True, potenda_factory=factory)

    assert result.status == "applied"
    assert dict(result.summary) == {"create": 2, "update": 1, "delete": 1}


def test_confirmed_sync_logs_the_timing_line_when_the_diff_has_changes(
    config_dir: str,
    cache_root: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    instance = resolve_sync_instance(SYNC_NAME, directory=config_dir)
    factory = _SpyFactory(cache_root=cache_root, rows=_fixture_creates())

    with caplog.at_level(logging.INFO, logger="infrahub_sync.execution"):
        execute_run(instance, operation="sync", confirm_writes=True, potenda_factory=factory)

    assert TIMING_LOG_PREFIX in caplog.text
    assert NO_DIFF_LOG not in caplog.text


def test_sync_over_an_unchanged_destination_skips_the_sync_and_the_timing_log(
    config_dir: str,
    cache_root: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """No diffs: no engine `sync`, no timing line — but the baseline is still persisted."""
    instance = resolve_sync_instance(SYNC_NAME, directory=config_dir)
    factory = _SpyFactory(cache_root=cache_root, rows=[])

    with caplog.at_level(logging.INFO, logger="infrahub_sync.execution"):
        result = execute_run(instance, operation="sync", confirm_writes=True, potenda_factory=factory)

    assert result.status == "no-change"
    assert result.changed is False
    assert dict(result.summary) == {"create": 0, "update": 0, "delete": 0}
    assert NO_DIFF_LOG in caplog.text
    assert TIMING_LOG_PREFIX not in caplog.text
    engine = factory.engine
    assert engine is not None
    assert engine.synced is False
    assert engine.baseline_persisted is True


def test_second_confirmed_sync_converges_to_no_change(
    config_dir: str,
    cache_root: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Idempotent reconciliation: apply, then re-run against the synchronized state."""
    instance = resolve_sync_instance(SYNC_NAME, directory=config_dir)
    factory = _ConvergingFactory(cache_root=cache_root, rows=_fixture_creates())

    # `caplog` captures for the whole test, so the two runs' records are separated
    # explicitly — otherwise the first run's timing line would still be in
    # `caplog.text` while the second run is being asserted about.
    with caplog.at_level(logging.INFO, logger="infrahub_sync.execution"):
        first = execute_run(instance, operation="sync", confirm_writes=True, potenda_factory=factory)
        first_logs = caplog.text
        caplog.clear()
        second = execute_run(instance, operation="sync", confirm_writes=True, potenda_factory=factory)
        second_logs = caplog.text

    assert first.status == "applied"
    assert first.changed is True
    assert dict(first.summary) == {"create": 5, "update": 0, "delete": 0}
    assert TIMING_LOG_PREFIX in first_logs

    assert second.status == "no-change"
    assert second.changed is False
    assert dict(second.summary) == {"create": 0, "update": 0, "delete": 0}
    assert second.run_id != first.run_id
    assert NO_DIFF_LOG in second_logs
    assert TIMING_LOG_PREFIX not in second_logs

    assert factory.engines[0].synced is True
    assert factory.engines[1].synced is False
    # Both runs record mode="sync"/status="applied" in run.json: run.json reports
    # that the sync lifecycle ran to completion, while RunResult.status reports
    # whether the destination actually differed.
    for engine in factory.engines:
        run_file = RunFile.load_or_default(engine.run_dir / "run.json")
        assert run_file.mode == "sync"
        assert run_file.status == "applied"
        assert run_file.summary == {"resources": 1, "mode": "serial"}


def test_follow_up_plan_after_a_confirmed_sync_reports_no_change(config_dir: str, cache_root: Path) -> None:
    """The DBA-005 convergence leg: a plan over the synchronized state is empty."""
    instance = resolve_sync_instance(SYNC_NAME, directory=config_dir)
    factory = _ConvergingFactory(cache_root=cache_root, rows=_fixture_creates())

    applied = execute_run(instance, operation="sync", confirm_writes=True, potenda_factory=factory)
    planned = execute_run(instance, operation="plan", potenda_factory=factory)

    assert applied.status == "applied"
    assert planned.operation == "plan"
    assert planned.status == "no-change"
    assert planned.changed is False
    assert dict(planned.summary) == {"create": 0, "update": 0, "delete": 0}
    assert RunFile.load_or_default(factory.engines[1].run_dir / "run.json").mode == "diff"


def test_confirmed_sync_through_the_remote_composition_returns_applied(config_dir: str, cache_root: Path) -> None:
    """The remote path reaches the same applied result the CLI serial branch does."""
    factory = _ConvergingFactory(cache_root=cache_root, rows=_fixture_creates())

    result = run_remote_request(
        SYNC_NAME,
        "sync",
        confirm_writes=True,
        config_directory=config_dir,
        _potenda_factory=factory,
    )

    assert result.operation == "sync"
    assert result.status == "applied"
    assert result.changed is True
    assert dict(result.summary) == {"create": 5, "update": 0, "delete": 0}
    assert Path(result.artifact_path).name == result.run_id
    assert factory.calls[0]["show_progress"] is False
    assert factory.engines[0].synced is True
