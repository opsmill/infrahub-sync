"""Unit coverage for the local-NetBox task logic that runs without Docker."""

from __future__ import annotations

import gzip
import hashlib
import json
import shlex
from contextlib import nullcontext
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
import yaml
from invoke import Context

from tasks import netbox

if TYPE_CHECKING:
    from collections.abc import Callable

    from invoke.tasks import Task

REQUIRED_VALUES = {
    "COMPOSE_PROJECT_NAME": "netbox-test",
    "NETBOX_PORT": "8082",
    "NETBOX_DB_PASSWORD": "netbox",
    "NETBOX_SECRET_KEY": "secret",
    "NETBOX_TOKEN_PEPPER": "pepper",
    "NETBOX_ADMIN_PASSWORD": "admin",
    "NETBOX_TOKEN_KEY": "devnetboxkey",
    "NETBOX_TOKEN": "devnetboxseedtoken0000000000000000000000",
}


@pytest.fixture(autouse=True)
def isolated_tier_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Isolate generated tier files and suppress live worker connections."""
    monkeypatch.setattr(netbox, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(netbox, "attach_dev_worker", lambda _context, _values: False)
    monkeypatch.setattr(netbox, "detach_dev_worker", lambda _context, _values: None)


def test_dataset_script_resolves_the_seed_dataset() -> None:
    assert netbox.dataset_script("seed") == netbox.DATASETS["seed"]


def test_dataset_script_refuses_an_unknown_dataset() -> None:
    with pytest.raises(netbox.NetboxError, match=r"unknown dataset 'nope'; expected one of \['demo', 'seed'\]"):
        netbox.dataset_script("nope")


def test_netbox_url_reads_the_configured_port() -> None:
    assert netbox.netbox_url({"NETBOX_PORT": "8082"}) == "http://localhost:8082"


def test_netbox_token_joins_the_v2_key_and_plaintext() -> None:
    values = {"NETBOX_TOKEN_KEY": "devnetboxkey", "NETBOX_TOKEN": "devnetboxseedtoken0000000000000000000000"}

    assert netbox.netbox_token(values) == "nbt_devnetboxkey.devnetboxseedtoken0000000000000000000000"


def test_load_netbox_env_reports_every_missing_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    local_env = tmp_path / "netbox.local.env"
    monkeypatch.setattr(netbox, "ENV_FILE", tmp_path / "missing.env")
    monkeypatch.setattr(netbox, "LOCAL_ENV_FILE", local_env)

    with pytest.raises(netbox.NetboxError, match="COMPOSE_PROJECT_NAME"):
        netbox.load_netbox_env()


def test_load_netbox_env_applies_local_overrides_after_the_shipped_defaults(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    shipped = tmp_path / "netbox.env"
    shipped.write_text("\n".join(f"{key}={value}" for key, value in REQUIRED_VALUES.items()), encoding="utf-8")
    local_env = tmp_path / "netbox.local.env"
    local_env.write_text("NETBOX_PORT=9999\n", encoding="utf-8")
    monkeypatch.setattr(netbox, "ENV_FILE", shipped)
    monkeypatch.setattr(netbox, "LOCAL_ENV_FILE", local_env)

    values = netbox.load_netbox_env()

    assert values["NETBOX_PORT"] == "9999"
    assert values["COMPOSE_PROJECT_NAME"] == "netbox-test"


def test_the_shipped_env_file_declares_every_required_key() -> None:
    """The committed defaults must be enough to run every task from a fresh clone."""
    values = netbox.load_netbox_env()

    assert set(REQUIRED_VALUES) <= values.keys()


def test_compose_builds_the_expected_docker_compose_invocation(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict[str, str], bool]] = []
    context = Context()
    monkeypatch.setattr(context, "cd", lambda _path: nullcontext())
    monkeypatch.setattr(context, "run", lambda command, *, env, pty: calls.append((command, env, pty)))

    netbox._compose(context, "config", REQUIRED_VALUES)

    assert calls == [
        (
            f"docker compose --project-name netbox-test --env-file {netbox.ENV_FILE} -f {netbox.COMPOSE_FILE} config",
            REQUIRED_VALUES,
            False,
        )
    ]


def test_reset_database_recreates_before_it_waits(monkeypatch: pytest.MonkeyPatch) -> None:
    """The reset step drops volumes, brings the stack back up, and only then waits.

    Order is the property under test: waiting before `up` would check a container that
    is not there yet, and seeding before either step would write to a database `down
    --volumes` is about to discard.
    """
    events: list[tuple[str, ...]] = []
    context = Context()

    monkeypatch.setattr(netbox, "_compose", lambda _context, arguments, _values: events.append(("compose", arguments)))
    monkeypatch.setattr(netbox, "_wait_for_http", lambda _url, description: events.append(("wait", description)))

    netbox.reset_database(context, REQUIRED_VALUES)

    assert events == [
        ("compose", "down --volumes"),
        ("compose", f"up --detach --wait --wait-timeout {netbox.WAIT_TIMEOUT_SECONDS}"),
        ("wait", "NetBox"),
    ]


def test_seed_resets_the_database_before_loading_the_dataset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reset before seeding and pass both credentials through the subprocess environment."""
    events: list[tuple[str, str]] = []
    environments: list[dict[str, str]] = []
    context = Context()

    monkeypatch.setattr(netbox, "load_netbox_env", lambda: REQUIRED_VALUES)
    monkeypatch.setattr(netbox, "reset_database", lambda _context, _values: events.append(("reset", "")))
    monkeypatch.setattr(context, "cd", lambda _path: nullcontext())

    def run(command: str, *, env: dict[str, str], **_kwargs: object) -> None:
        """Capture the command and its required environment without executing a subprocess."""
        events.append(("run", command))
        environments.append(env)

    monkeypatch.setattr(context, "run", run)

    cast("Task", netbox.seed).body(context, dataset="seed")

    assert events[0] == ("reset", "")
    run_event = events[1]
    assert run_event[0] == "run"
    assert str(netbox.DATASETS["seed"]) in run_event[1]
    assert "--tier S" in run_event[1]
    assert "--token" not in run_event[1]
    assert environments == [
        {"NETBOX_URL": netbox.netbox_url(REQUIRED_VALUES), "NETBOX_API_TOKEN": netbox.netbox_token(REQUIRED_VALUES)}
    ]


def test_seed_reports_the_tier_and_development_banner(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Report the loaded tier alongside the development connection banner."""
    context = Context()
    monkeypatch.setattr(netbox, "load_netbox_env", lambda: REQUIRED_VALUES)
    monkeypatch.setattr(netbox, "reset_database", lambda _context, _values: None)
    monkeypatch.setattr(context, "cd", lambda _path: nullcontext())
    monkeypatch.setattr(context, "run", lambda _command, **_kwargs: None)

    cast("Task", netbox.seed).body(context, dataset="seed")

    printed = capsys.readouterr().out
    assert "NetBox tier S ready" in printed
    assert f"URL:   {netbox.netbox_url(REQUIRED_VALUES)}" in printed
    assert f"Token: {netbox.netbox_token(REQUIRED_VALUES)}" in printed


def test_seed_refuses_an_unknown_dataset_before_touching_the_database(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    context = Context()
    monkeypatch.setattr(netbox, "load_netbox_env", lambda: REQUIRED_VALUES)
    monkeypatch.setattr(netbox, "reset_database", lambda _context, _values: events.append("reset"))

    with pytest.raises(netbox.NetboxError, match="unknown dataset"):
        cast("Task", netbox.seed).body(context, dataset="nope")

    assert events == []


def test_up_waits_for_netbox_and_prints_no_seed_step(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    events: list[str] = []
    context = Context()
    monkeypatch.setattr(netbox, "load_netbox_env", lambda: REQUIRED_VALUES)
    monkeypatch.setattr(netbox, "_compose", lambda *_args, **_kwargs: events.append("compose"))
    monkeypatch.setattr(netbox, "_wait_for_http", lambda *_args, **_kwargs: events.append("wait"))

    cast("Task", netbox.up).body(context)

    assert events == ["compose", "wait"]
    printed = capsys.readouterr().out
    assert "http://localhost:8082" in printed
    assert "nbt_devnetboxkey.devnetboxseedtoken0000000000000000000000" in printed


def test_down_removes_containers_and_volumes(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    context = Context()
    monkeypatch.setattr(netbox, "load_netbox_env", lambda: REQUIRED_VALUES)
    monkeypatch.setattr(netbox, "_compose", lambda _context, arguments, _values: calls.append(arguments))

    cast("Task", netbox.down).body(context)

    assert calls == ["down --volumes"]


PINNED_BYTES = b"-- pinned demo dump\n"
PINNED_SHA256 = hashlib.sha256(PINNED_BYTES).hexdigest()
DEMO_URL = "https://example.invalid/netbox-demo.sql"


def _download_writing(content: bytes, calls: list[str]) -> Callable[[str, Path], None]:
    def download(url: str, target: Path) -> None:
        calls.append(url)
        target.write_bytes(content)

    return download


def test_the_demo_sql_is_pinned_to_one_commit_and_checksum() -> None:
    assert netbox.DEMO_SQL_COMMIT in netbox.DEMO_SQL_URL
    assert netbox.DEMO_SQL_URL.startswith("https://raw.githubusercontent.com/netbox-community/netbox-demo-data/")
    assert len(netbox.DEMO_SQL_SHA256) == 64
    assert int(netbox.DEMO_SQL_SHA256, 16) >= 0


def test_the_demo_sql_and_the_local_package_live_in_the_gitignored_state_directory() -> None:
    """Both files are downloaded or generated; neither may ever be committed."""
    ignored = (netbox.REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()

    assert ".netbox/" in ignored
    for path in (netbox.DEMO_SQL_FILE, netbox.DEMO_SQL_RESTORE_FILE, netbox.LOCAL_PACKAGE):
        assert path.parent == netbox.REPO_ROOT / ".netbox"


def test_fetch_demo_sql_refuses_a_download_with_the_wrong_checksum(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    destination = tmp_path / "demo.sql"
    calls: list[str] = []
    monkeypatch.setattr(netbox, "_download", _download_writing(b"-- something else\n", calls))

    with pytest.raises(netbox.NetboxError, match=f"expected {PINNED_SHA256}; refusing to restore it"):
        netbox.fetch_demo_sql(destination, DEMO_URL, PINNED_SHA256)

    assert calls == [DEMO_URL]
    # Neither the refused content nor its partial file is left behind to be reused.
    assert list(tmp_path.iterdir()) == []


def test_fetch_demo_sql_keeps_a_download_with_the_pinned_checksum(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    destination = tmp_path / "state" / "demo.sql"
    calls: list[str] = []
    monkeypatch.setattr(netbox, "_download", _download_writing(PINNED_BYTES, calls))

    assert netbox.fetch_demo_sql(destination, DEMO_URL, PINNED_SHA256) == destination

    assert destination.read_bytes() == PINNED_BYTES
    assert sorted(path.name for path in destination.parent.iterdir()) == ["demo.sql"]


def test_fetch_demo_sql_reuses_a_verified_copy_without_downloading(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    destination = tmp_path / "demo.sql"
    destination.write_bytes(PINNED_BYTES)
    calls: list[str] = []
    monkeypatch.setattr(netbox, "_download", _download_writing(b"unused", calls))

    assert netbox.fetch_demo_sql(destination, DEMO_URL, PINNED_SHA256) == destination

    assert calls == []


def test_fetch_demo_sql_refuses_a_changed_copy_instead_of_downloading_over_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    destination = tmp_path / "demo.sql"
    destination.write_bytes(b"-- edited by hand\n")
    calls: list[str] = []
    monkeypatch.setattr(netbox, "_download", _download_writing(PINNED_BYTES, calls))

    with pytest.raises(netbox.NetboxError, match="delete it to download the pinned copy again"):
        netbox.fetch_demo_sql(destination, DEMO_URL, PINNED_SHA256)

    assert calls == []
    assert destination.read_bytes() == b"-- edited by hand\n"


def test_seed_demo_refuses_a_checksum_mismatch_before_touching_the_database(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    events: list[str] = []
    context = Context()
    real_fetch = netbox.fetch_demo_sql
    monkeypatch.setattr(netbox, "load_netbox_env", lambda: REQUIRED_VALUES)
    monkeypatch.setattr(netbox, "_download", _download_writing(b"-- tampered\n", []))
    monkeypatch.setattr(netbox, "fetch_demo_sql", lambda: real_fetch(tmp_path / "demo.sql", DEMO_URL, PINNED_SHA256))
    monkeypatch.setattr(netbox, "_compose", lambda _context, arguments, _values: events.append(arguments))
    monkeypatch.setattr(netbox, "_wait_for_http", lambda *_args, **_kwargs: events.append("wait"))

    with pytest.raises(netbox.NetboxError, match="refusing to restore it"):
        cast("Task", netbox.seed).body(context, dataset="demo")

    assert events == []


def test_seed_demo_restores_into_a_fresh_database_then_creates_the_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """NetBox itself starts only after the restore, so it never migrates an empty database.

    The role comes before the restore because the dump assigns its schema to `postgres`;
    the token comes after NetBox starts because it needs NetBox's own models.
    """
    sql_file = tmp_path / "demo.sql"
    sql_file.write_text(f"{netbox.DEMO_SQL_SEARCH_PATH_LINE}\nSELECT 1;\n", encoding="utf-8")
    restore_file = tmp_path / "demo.restore.sql"
    real_prepare = netbox.prepare_restore_sql
    events: list[str] = []
    netbox.record_tier("L")
    context = Context()
    monkeypatch.setattr(netbox, "load_netbox_env", lambda: REQUIRED_VALUES)
    monkeypatch.setattr(netbox, "fetch_demo_sql", lambda: sql_file)
    monkeypatch.setattr(netbox, "prepare_restore_sql", lambda source: real_prepare(source, restore_file))
    monkeypatch.setattr(netbox, "_compose", lambda _context, arguments, _values: events.append(arguments))
    monkeypatch.setattr(netbox, "_wait_for_http", lambda _url, description: events.append(f"wait {description}"))

    cast("Task", netbox.seed).body(context, dataset="demo")

    up = f"up --detach --wait --wait-timeout {netbox.WAIT_TIMEOUT_SECONDS}"
    psql = "exec -T netbox-database psql --quiet --username netbox --dbname netbox"
    assert events[:5] == [
        "down --volumes",
        f"{up} netbox-database netbox-redis",
        f"{psql} --command 'CREATE ROLE postgres NOLOGIN'",
        f"{psql} --set ON_ERROR_STOP=1 --single-transaction --output /dev/null < {restore_file}",
        up,
    ]
    assert events[5].startswith(
        "exec -T netbox /opt/netbox/netbox/manage.py shell --no-startup --no-imports --command "
    )
    assert "Token.objects.create(" in events[5]
    assert events[6:] == ["wait NetBox"]
    # The changed copy is removed once restored; only the verified dump stays.
    assert not restore_file.exists()
    assert sql_file.exists()
    assert not (netbox.STATE_DIR / "tier.json").exists()
    with pytest.raises(netbox.NetboxError, match="seed or restore"):
        cast("Task", netbox.dump).body(context, tier="L")
    printed = capsys.readouterr().out
    assert "http://localhost:8082" in printed
    assert "nbt_devnetboxkey.devnetboxseedtoken0000000000000000000000" in printed


def test_seed_demo_refuses_a_dump_without_the_search_path_line_before_touching_the_database(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sql_file = tmp_path / "demo.sql"
    sql_file.write_text("SELECT 1;\n", encoding="utf-8")
    restore_file = tmp_path / "demo.restore.sql"
    real_prepare = netbox.prepare_restore_sql
    events: list[str] = []
    monkeypatch.setattr(netbox, "load_netbox_env", lambda: REQUIRED_VALUES)
    monkeypatch.setattr(netbox, "fetch_demo_sql", lambda: sql_file)
    monkeypatch.setattr(netbox, "prepare_restore_sql", lambda source: real_prepare(source, restore_file))
    monkeypatch.setattr(netbox, "_compose", lambda _context, arguments, _values: events.append(arguments))
    monkeypatch.setattr(netbox, "_wait_for_http", lambda *_args, **_kwargs: events.append("wait"))

    with pytest.raises(netbox.NetboxError, match="exactly once, found 0"):
        cast("Task", netbox.seed).body(Context(), dataset="demo")

    assert events == []
    assert not restore_file.exists()


def test_restore_demo_database_removes_the_changed_copy_when_the_restore_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sql_file = tmp_path / "demo.sql"
    sql_file.write_text(f"{netbox.DEMO_SQL_SEARCH_PATH_LINE}\nSELECT 1;\n", encoding="utf-8")
    restore_file = tmp_path / "demo.restore.sql"
    real_prepare = netbox.prepare_restore_sql
    monkeypatch.setattr(netbox, "prepare_restore_sql", lambda source: real_prepare(source, restore_file))

    def compose(_context: Context, arguments: str, _values: dict[str, str]) -> None:
        if "ON_ERROR_STOP" in arguments:
            assert restore_file.exists()
            msg = "restore failed"
            raise netbox.NetboxError(msg)

    monkeypatch.setattr(netbox, "_compose", compose)

    with pytest.raises(netbox.NetboxError, match="restore failed"):
        netbox.restore_demo_database(Context(), REQUIRED_VALUES, sql_file)

    assert not restore_file.exists()
    assert sql_file.exists()


def test_prepare_restore_sql_changes_only_the_search_path_line(tmp_path: Path) -> None:
    sql_file = tmp_path / "demo.sql"
    sql_file.write_text(f"SET lock_timeout = 0;\n{netbox.DEMO_SQL_SEARCH_PATH_LINE}\nSELECT 1;\n", encoding="utf-8")

    restored = netbox.prepare_restore_sql(sql_file, tmp_path / "out" / "demo.restore.sql")

    assert restored.read_text(encoding="utf-8") == (
        f"SET lock_timeout = 0;\n{netbox.DEMO_SQL_RESTORE_SEARCH_PATH_LINE}\nSELECT 1;\n"
    )


@pytest.mark.parametrize("occurrences", [0, 2])
def test_prepare_restore_sql_refuses_a_dump_without_exactly_one_search_path_line(
    tmp_path: Path, occurrences: int
) -> None:
    sql_file = tmp_path / "demo.sql"
    sql_file.write_text(f"{netbox.DEMO_SQL_SEARCH_PATH_LINE}\n" * occurrences + "SELECT 1;\n", encoding="utf-8")

    with pytest.raises(netbox.NetboxError, match=f"exactly once, found {occurrences}"):
        netbox.prepare_restore_sql(sql_file, tmp_path / "demo.restore.sql")


def test_local_package_text_replaces_only_the_two_urls() -> None:
    shipped = netbox.SHIPPED_PACKAGE.read_text(encoding="utf-8")

    local = netbox.local_package_text(shipped, "http://localhost:8082", "http://localhost:8080")

    shipped_package = yaml.safe_load(shipped)
    local_package = yaml.safe_load(local)
    assert local_package["configuration"]["source"]["settings"]["url"] == "http://localhost:8082"
    assert local_package["configuration"]["destination"]["settings"]["url"] == "http://localhost:8080"
    local_package["configuration"]["source"]["settings"]["url"] = netbox.SHIPPED_NETBOX_URL
    local_package["configuration"]["destination"]["settings"]["url"] = netbox.SHIPPED_INFRAHUB_URL
    assert local_package == shipped_package
    assert len(local.splitlines()) == len(shipped.splitlines())


def test_local_package_text_accepts_a_netbox_url_equal_to_the_shipped_infrahub_url() -> None:
    """A local NetBox on port 8000 must not make the shipped Infrahub line count twice."""
    shipped = netbox.SHIPPED_PACKAGE.read_text(encoding="utf-8")

    local = yaml.safe_load(netbox.local_package_text(shipped, netbox.SHIPPED_INFRAHUB_URL, "http://localhost:8080"))

    assert local["configuration"]["source"]["settings"]["url"] == netbox.SHIPPED_INFRAHUB_URL
    assert local["configuration"]["destination"]["settings"]["url"] == "http://localhost:8080"


def test_local_package_text_refuses_a_package_without_the_public_demo_url() -> None:
    with pytest.raises(netbox.NetboxError, match="exactly once, found 0"):
        netbox.local_package_text('url: "http://localhost:8000"\n', "http://localhost:8082", "http://localhost:8080")


def test_demo_package_writes_the_local_copy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    destination = tmp_path / "state" / "from-netbox.local.yml"
    monkeypatch.setattr(netbox, "load_netbox_env", lambda: REQUIRED_VALUES)
    monkeypatch.setattr(netbox, "STATE_DIR", destination.parent)
    monkeypatch.setattr(netbox, "LOCAL_PACKAGE", destination)

    cast("Task", netbox.demo_package).body(Context(), infrahub_url="http://localhost:18080")

    written = yaml.safe_load(destination.read_text(encoding="utf-8"))
    assert written["configuration"]["source"]["settings"]["url"] == "http://localhost:8082"
    assert written["configuration"]["destination"]["settings"]["url"] == "http://localhost:18080"
    assert str(destination) in capsys.readouterr().out


@pytest.mark.parametrize(("dataset", "tier"), [("seed", "X"), ("demo", "M"), ("demo", "L")])
def test_seed_rejects_tier_errors_before_reset(monkeypatch: pytest.MonkeyPatch, dataset: str, tier: str) -> None:
    """Reject unsupported tiers before resetting the database."""
    events: list[str] = []
    monkeypatch.setattr(netbox, "reset_database", lambda *_args: events.append("reset"))
    with pytest.raises((ValueError, netbox.NetboxError), match="tier"):
        cast("Task", netbox.seed).body(Context(), dataset=dataset, tier=tier)
    assert not events


@pytest.mark.parametrize("tier", ["S", "M", "L"])
def test_tier_dump_paths_are_isolated_and_validated(tier: str) -> None:
    """Keep each valid tier dump separate and reject invalid tier names."""
    sql, sidecar = netbox.tier_paths(tier)
    assert sql == netbox.STATE_DIR / "dumps" / f"netbox-tier-{tier}.sql.gz"
    assert sidecar == sql.with_suffix(".json")
    with pytest.raises(ValueError, match="tier"):
        netbox.tier_paths("../invalid")


def write_tier_dump(
    tier: str,
    *,
    content: bytes = (netbox.DEMO_SQL_SEARCH_PATH_LINE + "\nSELECT 1;\n").encode(),
    image: str | None = None,
) -> Path:
    """Write a synthetic compressed dump and its matching metadata."""
    sql, metadata = netbox.tier_paths(tier)
    sql.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(sql, "wb") as handle:
        handle.write(content)
    metadata.write_text(
        json.dumps(
            {
                "format_version": 1,
                "tier": tier,
                "image": image or netbox.netbox_image(),
                "sha256": netbox.sha256_of(sql),
            }
        )
    )
    return sql


def test_restore_refuses_missing_dump_before_touching_database(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reject a missing dump before running database commands."""
    calls: list[str] = []
    monkeypatch.setattr(netbox, "_compose", lambda _context, args, _values: calls.append(args))
    with pytest.raises(netbox.NetboxError, match="missing tier M dump"):
        cast("Task", netbox.restore).body(Context(), tier="M")
    assert not calls


@pytest.mark.parametrize("problem", ["image", "tier", "checksum", "malformed", "version"])
def test_restore_refuses_invalid_sidecar_before_touching_database(
    monkeypatch: pytest.MonkeyPatch, problem: str
) -> None:
    """Reject mismatched dump metadata before running database commands."""
    sql = write_tier_dump("M")
    metadata = sql.with_suffix(".json")
    info = json.loads(metadata.read_text())
    if problem == "image":
        info["image"] = "different-image"
    elif problem == "tier":
        info["tier"] = "S"
    elif problem == "checksum":
        info["sha256"] = "wrong"
    elif problem == "version":
        info["format_version"] = 2
    metadata.write_text("invalid json" if problem == "malformed" else json.dumps(info))
    calls: list[str] = []
    monkeypatch.setattr(netbox, "_compose", lambda _context, args, _values: calls.append(args))
    with pytest.raises(netbox.NetboxError):
        cast("Task", netbox.restore).body(Context(), tier="M")
    assert not calls


def test_tier_restore_orders_database_load_before_netbox_and_reapplies_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    """Restore before starting NetBox and recreate development credentials."""
    sql = write_tier_dump("M")
    calls: list[str] = []
    context = Context()

    def refuse_subprocess(*_args: object, **_kwargs: object) -> None:
        """Fail if the restore test attempts a host subprocess."""
        pytest.fail("restore unit test attempted a host subprocess")

    monkeypatch.setattr(context, "run", refuse_subprocess)

    def detach(ctx: Context, values: dict[str, str]) -> None:
        """Record worker detachment with the original context and settings."""
        assert ctx is context
        assert values == REQUIRED_VALUES
        calls.append("detach")

    def attach(ctx: Context, values: dict[str, str]) -> bool:
        """Record worker attachment with the original context and settings."""
        assert ctx is context
        assert values == REQUIRED_VALUES
        calls.append("attach")
        return True

    monkeypatch.setattr(netbox, "detach_dev_worker", detach)
    monkeypatch.setattr(netbox, "attach_dev_worker", attach)
    monkeypatch.setattr(netbox, "load_netbox_env", lambda: REQUIRED_VALUES)
    monkeypatch.setattr(netbox, "_compose", lambda _context, args, _values: calls.append(args))
    monkeypatch.setattr(netbox, "_wait_for_http", lambda *_args: calls.append("wait"))
    cast("Task", netbox.restore).body(context, tier="M")
    assert calls.pop(0) == "detach"
    assert calls.pop() == "attach"
    assert calls[0] == "down --volumes"
    assert calls[1].endswith("netbox-database netbox-redis")
    assert "psql" in calls[2]
    assert "ON_ERROR_STOP=1 --single-transaction" in calls[2]
    assert calls[3] == f"up --detach --wait --wait-timeout {netbox.WAIT_TIMEOUT_SECONDS}"
    assert "User.objects.get_or_create" in calls[4]
    assert "Token.objects.filter" in calls[4]
    assert "Token.objects.create" in calls[4]
    assert calls[5] == "wait"
    assert not sql.with_suffix(".restore.sql").exists()
    assert json.loads((netbox.STATE_DIR / "tier.json").read_text())["tier"] == "M"


def test_dump_compresses_with_checksum_metadata_and_no_raw_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """Publish a compressed dump with verified metadata and remove raw files."""
    netbox.record_tier("M")
    calls: list[str] = []
    context = Context()
    monkeypatch.setattr(context, "cd", lambda _path: nullcontext())
    monkeypatch.setattr(context, "run", lambda cmd, **_kwargs: calls.append(cmd))

    def compose(_context: Context, arguments: str, _values: dict[str, str]) -> None:
        """Capture the dump command and write synthetic database output."""
        calls.append(arguments)
        Path(shlex.split(arguments)[-1]).write_bytes(b"SELECT 42;\n")

    monkeypatch.setattr(netbox, "_compose", compose)
    monkeypatch.setattr(netbox, "load_netbox_env", lambda: REQUIRED_VALUES)
    cast("Task", netbox.dump).body(context, tier="M")
    sql, metadata = netbox.tier_paths("M")
    assert calls[0].endswith("--tier M --verify-only")
    assert "pg_dump" in calls[1]
    assert "--no-owner --no-privileges" in calls[1]
    assert gzip.decompress(sql.read_bytes()) == b"SELECT 42;\n"
    assert json.loads(metadata.read_text())["sha256"] == netbox.sha256_of(sql)
    assert netbox.verify_tier_dump("M") == sql
    assert not list(sql.parent.glob("*partial*"))


def test_dump_refuses_other_tier_or_changed_phase(monkeypatch: pytest.MonkeyPatch) -> None:
    """Refuse a dump when recorded tier or mutation phase does not match."""
    calls: list[str] = []
    monkeypatch.setattr(netbox, "_compose", lambda _context, args, _values: calls.append(args))
    netbox.record_tier("S")
    with pytest.raises(netbox.NetboxError, match="differs"):
        cast("Task", netbox.dump).body(Context(), tier="L")
    netbox.record_tier("S", phase="changed")
    with pytest.raises(netbox.NetboxError, match="changes were applied"):
        cast("Task", netbox.dump).body(Context(), tier="S")
    assert calls == []


def test_change_task_passes_credentials_only_in_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pass change credentials through the environment and record its phase."""
    calls: list[tuple[str, dict[str, str]]] = []
    context = Context()
    monkeypatch.setattr(netbox, "load_netbox_env", lambda: REQUIRED_VALUES)
    monkeypatch.setattr(context, "cd", lambda _path: nullcontext())
    monkeypatch.setattr(context, "run", lambda command, *, env, **_kwargs: calls.append((command, env)))
    cast("Task", netbox.change).body(context, tier="L", force=True)
    assert calls[0][0].endswith("--tier L --force")
    assert netbox.netbox_token(REQUIRED_VALUES) not in calls[0][0]
    assert calls[0][1]["NETBOX_API_TOKEN"] == netbox.netbox_token(REQUIRED_VALUES)
    assert json.loads((netbox.STATE_DIR / "tier.json").read_text())["phase"] == "changed"


def test_restore_refuses_invalid_gzip_before_reset_and_cleans_raw_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reject corrupt compressed data before reset and remove extraction files."""
    sql = write_tier_dump("S")
    sql.write_bytes(b"not gzip")
    metadata = sql.with_suffix(".json")
    info = json.loads(metadata.read_text())
    info["sha256"] = netbox.sha256_of(sql)
    metadata.write_text(json.dumps(info))
    calls: list[str] = []
    monkeypatch.setattr(netbox, "_compose", lambda _context, args, _values: calls.append(args))
    with pytest.raises(gzip.BadGzipFile):
        cast("Task", netbox.restore).body(Context(), tier="S")
    assert not calls
    assert not sql.with_suffix(".restore.sql").exists()
