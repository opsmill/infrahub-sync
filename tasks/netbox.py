"""Local, disposable NetBox with two datasets: `seed` and the official NetBox `demo` data.

`invoke netbox.up` starts a disposable local NetBox instance and prints its URL and
development token. `invoke netbox.seed` resets the database, loads a named dataset, and
prints the same URL and token banner:

- `seed` (the default) is the deterministic dataset
  `tests/integration/test_saved_plan_apply_integration.py` requires.
- `demo` is the official NetBox demo data, which the `from-netbox` example check runs
  against. The SQL dump is downloaded at a pinned commit, checked against a pinned SHA-256,
  and restored in place of the whole database. It is never committed.

`invoke netbox.demo-package` writes a copy of the shipped `from-netbox` package that
points at this local NetBox and the preview Infrahub. `invoke netbox.down` removes the
containers and their volumes.

Configuration ships in `development/netbox/netbox.env` (no secrets -- local-only
defaults); personal overrides belong in the gitignored
`development/netbox/netbox.local.env`. Downloaded and generated files live in the
gitignored `.netbox/` directory at the repository root.
"""

from __future__ import annotations

import hashlib
import json
import shlex
import time
from pathlib import Path
from typing import TYPE_CHECKING

from invoke import Context, task

from .preview import load_preview_env, preview_urls
from .utils import ESCAPED_REPO_PATH

if TYPE_CHECKING:
    import httpx

NAMESPACE = "INFRAHUB-SYNC-NETBOX"
REPO_ROOT = Path(__file__).parent.parent.resolve()
DEV_DIR = REPO_ROOT / "development" / "netbox"
ENV_FILE = DEV_DIR / "netbox.env"
LOCAL_ENV_FILE = DEV_DIR / "netbox.local.env"
COMPOSE_FILE = DEV_DIR / "docker-compose.netbox.yml"
DATASETS_DIR = DEV_DIR / "datasets"
STATE_DIR = REPO_ROOT / ".netbox"

# One seeder script per API-seeded dataset name. The `demo` dataset is not seeded through
# the API: it restores a SQL dump in place of the whole database (see `restore_demo_database`).
DATASETS: dict[str, Path] = {
    "seed": DATASETS_DIR / "seed_netbox.py",
}
DEFAULT_DATASET = "seed"
DEMO_DATASET = "demo"
DATASET_NAMES = sorted([*DATASETS, DEMO_DATASET])

# The official NetBox demo data (netbox-community/netbox-demo-data, MIT license), pinned to
# one commit and one checksum. The dump matches the NetBox v4.7 image in
# `docker-compose.netbox.yml`; change the commit, file, checksum, and image together.
DEMO_SQL_COMMIT = "faeae5904a29ce6ad519a3de270ca218a7b050f9"
DEMO_SQL_URL = (
    f"https://raw.githubusercontent.com/netbox-community/netbox-demo-data/{DEMO_SQL_COMMIT}/sql/netbox-demo-v4.7.sql"
)
DEMO_SQL_SHA256 = "e9846c189c4c5055abfe52d2ca0b55ff089d815c9b0c20614faba94b9c8f5180"
DEMO_SQL_FILE = STATE_DIR / "netbox-demo-v4.7.sql"
# pg_dump empties `search_path` for the whole restore, but prints the demo's trigger
# conditions (`old.path IS DISTINCT FROM new.path` on `public.ltree`) without qualifying the
# operator, so PostgreSQL cannot resolve them and the restore stops. The restored copy keeps
# `public` on the path instead; every object in the dump is already schema-qualified.
DEMO_SQL_SEARCH_PATH_LINE = "SELECT pg_catalog.set_config('search_path', '', false);"
DEMO_SQL_RESTORE_SEARCH_PATH_LINE = "SELECT pg_catalog.set_config('search_path', 'public', false);"
DEMO_SQL_RESTORE_FILE = STATE_DIR / "netbox-demo-v4.7.restore.sql"

# The dump contains the demo's own `admin` user, so the image's superuser bootstrap skips it
# and creates no API token. This runs inside the NetBox container after the restore and
# gives that user the development password and token from `netbox.env`, which the
# container already receives as its `SUPERUSER_*` settings.
DEMO_ADMIN_SETUP_SCRIPT = """\
from os import environ

from users.choices import TokenVersionChoices
from users.models import Token, User

user = User.objects.get(username=environ["SUPERUSER_NAME"])
user.set_password(environ["SUPERUSER_PASSWORD"])
user.save()
Token.objects.create(
    user=user,
    token=environ["SUPERUSER_API_TOKEN"],
    key=environ["SUPERUSER_API_KEY"],
    version=TokenVersionChoices.V2,
)
print(f"Development API token created for {user.username!r}")
"""

# The shipped `from-netbox` package names the public NetBox demo and a default Infrahub.
# `netbox.demo-package` replaces exactly these two lines and leaves the rest byte for byte.
SHIPPED_PACKAGE = REPO_ROOT / "examples" / "netbox_to_infrahub" / "package.yml"
LOCAL_PACKAGE = STATE_DIR / "from-netbox.local.yml"
SHIPPED_NETBOX_URL = "https://demo.netbox.dev"
SHIPPED_INFRAHUB_URL = "http://localhost:8000"
# NetBox's first migration run took roughly 16 minutes on the reference machine, right at
# the previous 960s ceiling -- this leaves real headroom instead of racing it.
WAIT_TIMEOUT_SECONDS = 1800

_REQUIRED_ENV_KEYS = (
    "COMPOSE_PROJECT_NAME",
    "NETBOX_PORT",
    "NETBOX_DB_PASSWORD",
    "NETBOX_SECRET_KEY",
    "NETBOX_TOKEN_PEPPER",
    "NETBOX_ADMIN_PASSWORD",
    "NETBOX_TOKEN_KEY",
    "NETBOX_TOKEN",
)


class NetboxError(RuntimeError):
    """Raised when the local NetBox environment cannot reach a required state."""


def load_netbox_env() -> dict[str, str]:
    """Read settings from `netbox.env`, then `netbox.local.env` overrides."""
    values: dict[str, str] = {}
    for env_file in (ENV_FILE, LOCAL_ENV_FILE):
        if not env_file.exists():
            continue
        for raw_line in env_file.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    missing = set(_REQUIRED_ENV_KEYS) - values.keys()
    if missing:
        msg = f"{ENV_FILE} is missing required keys: {sorted(missing)}"
        raise NetboxError(msg)
    return values


def netbox_url(values: dict[str, str]) -> str:
    """The local NetBox base URL for the configured host port."""
    return f"http://localhost:{values['NETBOX_PORT']}"


def netbox_token(values: dict[str, str]) -> str:
    """The client-facing v2 API token, NetBox's `nbt_<key>.<token>` shape.

    Built from `NETBOX_TOKEN_KEY` (the token's public key component) and `NETBOX_TOKEN`
    (its plaintext secret), the two halves the container's superuser bootstrap accepts
    (`SUPERUSER_API_KEY` / `SUPERUSER_API_TOKEN`).
    """
    return f"nbt_{values['NETBOX_TOKEN_KEY']}.{values['NETBOX_TOKEN']}"


def dataset_script(dataset: str) -> Path:
    """The seeder script for a dataset name, or a clear error for an unknown one."""
    try:
        return DATASETS[dataset]
    except KeyError:
        msg = f"unknown dataset {dataset!r}; expected one of {DATASET_NAMES}"
        raise NetboxError(msg) from None


def _compose(context: Context, arguments: str, values: dict[str, str]) -> None:
    command = (
        f"docker compose --project-name {shlex.quote(values['COMPOSE_PROJECT_NAME'])} "
        f"--env-file {shlex.quote(str(ENV_FILE))} -f {shlex.quote(str(COMPOSE_FILE))} {arguments}"
    )
    with context.cd(ESCAPED_REPO_PATH):
        context.run(command, env=values, pty=False)


_SERVER_ERROR_FLOOR = 500


def _wait_for_http(url: str, description: str, timeout: int = WAIT_TIMEOUT_SECONDS) -> None:
    import httpx  # noqa: PLC0415 -- lazy so importing the tasks package never requires httpx

    print(f" - [{NAMESPACE}] Waiting for {description} at {url}")
    deadline = time.monotonic() + timeout
    last_error = "no attempt made"
    while time.monotonic() < deadline:
        try:
            response: httpx.Response = httpx.get(url, timeout=5)
        except httpx.HTTPError as exc:
            last_error = str(exc)
        else:
            if response.status_code < _SERVER_ERROR_FLOOR:
                return
            last_error = f"HTTP {response.status_code}"
        time.sleep(3)
    msg = f"{description} did not become ready within {timeout}s (last: {last_error})"
    raise NetboxError(msg)


def reset_database(context: Context, values: dict[str, str]) -> None:
    """Empty NetBox by recreating its containers from a fresh volume.

    `docker compose down --volumes` drops the Postgres data volume; the following `up`
    recreates NetBox against an empty database. Every seeder script under `DATASETS`
    asserts emptiness before writing, so a dataset load always starts from nothing —
    this is what makes loading a dataset safe to repeat.
    """
    print(f" - [{NAMESPACE}] Resetting the local NetBox database")
    _compose(context, "down --volumes", values)
    _compose(context, f"up --detach --wait --wait-timeout {WAIT_TIMEOUT_SECONDS}", values)
    _wait_for_http(f"{netbox_url(values)}/api/", "NetBox")


def sha256_of(path: Path) -> str:
    """The hex SHA-256 of a file, read in chunks."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_sha256(path: Path, expected: str, description: str) -> None:
    """Refuse a file whose SHA-256 is not the pinned one."""
    actual = sha256_of(path)
    if actual != expected:
        msg = f"{description} has SHA-256 {actual}, expected {expected}; refusing to restore it"
        raise NetboxError(msg)


def _download(url: str, target: Path) -> None:
    import httpx  # noqa: PLC0415 -- lazy so importing the tasks package never requires httpx

    print(f" - [{NAMESPACE}] Downloading {url}")
    try:
        with httpx.stream("GET", url, follow_redirects=True, timeout=60) as response:
            response.raise_for_status()
            with target.open("wb") as handle:
                for chunk in response.iter_bytes():
                    handle.write(chunk)
    except httpx.HTTPError as exc:
        msg = f"could not download {url}: {exc}"
        raise NetboxError(msg) from exc


def fetch_demo_sql(
    destination: Path = DEMO_SQL_FILE, url: str = DEMO_SQL_URL, expected_sha256: str = DEMO_SQL_SHA256
) -> Path:
    """Return the pinned demo SQL dump, downloading it once, and refuse any other content.

    A download is written beside the destination and moved into place only after its
    checksum matches, so an interrupted or altered download never becomes the cached copy.
    A cached copy is checked again on every use; if it no longer matches, this refuses
    rather than silently downloading over it.
    """
    if destination.exists():
        verify_sha256(destination, expected_sha256, f"{destination} (delete it to download the pinned copy again)")
        print(f" - [{NAMESPACE}] Using the verified demo SQL at {destination}")
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(f"{destination.name}.partial")
    try:
        _download(url, partial)
        verify_sha256(partial, expected_sha256, f"the download from {url}")
    except NetboxError:
        partial.unlink(missing_ok=True)
        raise
    partial.replace(destination)
    print(f" - [{NAMESPACE}] Verified SHA-256 {expected_sha256}")
    return destination


def prepare_restore_sql(sql_file: Path, destination: Path = DEMO_SQL_RESTORE_FILE) -> Path:
    """Write the copy of the verified dump that is actually restored.

    The only change is the `search_path` line described at `DEMO_SQL_SEARCH_PATH_LINE`. It
    must appear exactly once; the dump is pinned, so anything else means the wrong file.
    """
    text = sql_file.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    matches = [index for index, line in enumerate(lines) if line.rstrip("\n") == DEMO_SQL_SEARCH_PATH_LINE]
    if len(matches) != 1:
        msg = f"{sql_file} must contain {DEMO_SQL_SEARCH_PATH_LINE!r} exactly once, found {len(matches)}"
        raise NetboxError(msg)
    lines[matches[0]] = f"{DEMO_SQL_RESTORE_SEARCH_PATH_LINE}\n"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("".join(lines), encoding="utf-8")
    return destination


def restore_demo_database(context: Context, values: dict[str, str], sql_file: Path) -> None:
    """Replace the NetBox database with the demo dump, then give it the development token.

    The volume is dropped and only PostgreSQL and Valkey start, so NetBox never migrates
    an empty database first. The dump assigns its schema to a `postgres` role, which this
    image does not have, so an empty role of that name is created before the restore. The
    restore runs in one transaction and stops at the first error. NetBox then starts
    against the restored data and applies only migrations newer than the dump. The
    restored copy of the dump is deleted once the restore finishes or fails.
    """
    restore_file = prepare_restore_sql(sql_file)
    print(f" - [{NAMESPACE}] Resetting the local NetBox database")
    _compose(context, "down --volumes", values)
    _compose(context, f"up --detach --wait --wait-timeout {WAIT_TIMEOUT_SECONDS} netbox-database netbox-redis", values)
    psql = "exec -T netbox-database psql --quiet --username netbox --dbname netbox"
    _compose(context, f"{psql} --command {shlex.quote('CREATE ROLE postgres NOLOGIN')}", values)
    print(f" - [{NAMESPACE}] Restoring {sql_file}")
    try:
        _compose(
            context,
            f"{psql} --set ON_ERROR_STOP=1 --single-transaction --output /dev/null < {shlex.quote(str(restore_file))}",
            values,
        )
    finally:
        # The copy is regenerated from the verified dump on every restore; keep only the dump.
        restore_file.unlink(missing_ok=True)
    _compose(context, f"up --detach --wait --wait-timeout {WAIT_TIMEOUT_SECONDS}", values)
    _compose(
        context,
        "exec -T netbox /opt/netbox/netbox/manage.py shell --no-startup --no-imports "
        f"--command {shlex.quote(DEMO_ADMIN_SETUP_SCRIPT)}",
        values,
    )
    _wait_for_http(f"{netbox_url(values)}/api/", "NetBox")


def local_package_text(shipped: str, netbox: str, infrahub: str) -> str:
    """The shipped package text with its NetBox and Infrahub URLs replaced.

    Each shipped URL must appear exactly once as a `url:` setting; anything else means the
    shipped package changed shape, and a guessed replacement could point the copy at the
    public demo.
    """
    replacements = {
        f"url: {json.dumps(SHIPPED_NETBOX_URL)}": f"url: {json.dumps(netbox)}",
        f"url: {json.dumps(SHIPPED_INFRAHUB_URL)}": f"url: {json.dumps(infrahub)}",
    }
    # Count both lines in the shipped text before replacing either: a replacement URL can
    # equal the other shipped URL (a local NetBox on port 8000), and counting after the first
    # replacement would then blame the shipped package.
    for line in replacements:
        count = shipped.count(line)
        if count != 1:
            msg = f"{SHIPPED_PACKAGE} must contain {line!r} exactly once, found {count}"
            raise NetboxError(msg)
    lines = shipped.splitlines(keepends=True)
    for index, line in enumerate(lines):
        for current, replacement in replacements.items():
            if current in line:
                lines[index] = line.replace(current, replacement)
                break
    return "".join(lines)


def _print_ready_banner(values: dict[str, str]) -> None:
    print(f" - [{NAMESPACE}] NetBox ready")
    print(f"     URL:   {netbox_url(values)}")
    print(f"     Token: {netbox_token(values)}")


@task
def up(context: Context) -> None:
    """Bring up the disposable local NetBox, then print its URL and development token."""
    values = load_netbox_env()
    _compose(context, f"up --detach --wait --wait-timeout {WAIT_TIMEOUT_SECONDS}", values)
    _wait_for_http(f"{netbox_url(values)}/api/", "NetBox")
    _print_ready_banner(values)


@task
def seed(context: Context, dataset: str = DEFAULT_DATASET) -> None:
    """Reset the database, then load the named dataset: `seed` (default) or `demo`."""
    if dataset == DEMO_DATASET:
        values = load_netbox_env()
        # Verified before anything is reset: a refused download leaves NetBox as it was.
        sql_file = fetch_demo_sql()
        restore_demo_database(context, values, sql_file)
        _print_ready_banner(values)
        return
    script = dataset_script(dataset)
    values = load_netbox_env()
    reset_database(context, values)
    print(f" - [{NAMESPACE}] Loading the {dataset!r} dataset from {script}")
    with context.cd(ESCAPED_REPO_PATH):
        context.run(
            f"uv run python {shlex.quote(str(script))} "
            f"--url {shlex.quote(netbox_url(values))} --token {shlex.quote(netbox_token(values))}",
            pty=False,
        )
    _print_ready_banner(values)


@task(name="demo-package")
def demo_package(_context: Context, infrahub_url: str = "") -> None:
    """Write a copy of the `from-netbox` package for this NetBox and the preview Infrahub.

    The copy keeps the shipped mapping and credential references and changes only the two
    URLs. `--infrahub-url` defaults to the preview Infrahub address.
    """
    values = load_netbox_env()
    destination = infrahub_url or preview_urls(load_preview_env())["infrahub"]
    text = local_package_text(SHIPPED_PACKAGE.read_text(encoding="utf-8"), netbox_url(values), destination)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    LOCAL_PACKAGE.write_text(text, encoding="utf-8")
    print(f" - [{NAMESPACE}] Wrote {LOCAL_PACKAGE}")
    print(f"     NetBox:   {netbox_url(values)}")
    print(f"     Infrahub: {destination}")


@task
def down(context: Context) -> None:
    """Stop the local NetBox and remove its containers and data volumes."""
    values = load_netbox_env()
    _compose(context, "down --volumes", values)
    print(f" - [{NAMESPACE}] Local NetBox stopped and data volumes removed")
