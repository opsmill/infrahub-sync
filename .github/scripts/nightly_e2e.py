"""Run and report the disposable, dispatch-only nightly end-to-end suites."""

# ruff: noqa: INP001 -- GitHub Actions runs this script directly.

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess  # noqa: S404 -- fixed commands run against this checkout's disposable services
import sys
import tempfile
import time
import xml.etree.ElementTree as ET  # noqa: S405 -- only local pytest reports are parsed
from hashlib import sha256
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
REPORTS = ROOT / ".dev" / "nightly-e2e"
SUITES = ("integration", "smoke", "saved-plan", "from-netbox", "v2-gate")
EXPECTED_DEMO_OPERATIONS = 1688
SCHEMA_SHA256 = "b74a0ff09cde3aafb1275293be7523f1b377d64073c43aca3ccc14cbe4c5a025"
# The v2 release whose writes the first V3 plan is compared with. v1 and v2 shipped only as
# the `infrahub-sync` package on PyPI, so the gate installs it from there, as a customer did.
# The gate pins 2.0.1, the latest v2 release on PyPI (product owner decision, 2026-11-13).
# The configuration it runs is the NetBox example of the `2.0.2` tag, because the 2.0.1
# example maps kinds that the schema the gate loads does not have.
V2_VERSION = "2.0.1"
# v2 does not depend on pynetbox; its NetBox users installed it beside the package. The
# version is the one this repository locks.
V2_PYNETBOX = "pynetbox==7.8.0"
V2_EXAMPLE = ROOT / "tests" / "data" / "v2_examples" / "netbox_to_infrahub" / "config.yml"
V2_GATE_BRANCH = "v2-gate"
EXPECTED_DELETES = ROOT / "tests" / "data" / "v2_gate" / "expected_deletes.yml"
GATE_RESULT = "gate-result.json"
UNCOUNTED = "records left out and problems cannot be counted on this build"
DELETES_NOT_COMPUTED = "the plan did not compute delete operations, so an unexpected delete cannot be ruled out"
# The value an update finding gives for a field whose current destination value was not read.
NOT_READ = "<not read>"
_OPERATION_LINE = re.compile(
    r"^(?P<operation_id>\S+) (?P<action>create|update|delete) (?P<kind>\S+)(?: (?P<identity>.*?))?(?: \(not executed\))?$"
)
_DELETE_DISCLOSURE = re.compile(
    r"^(?:\d+ delete operation\(s\) are recorded|0 delete operations are recorded|Delete operations were NOT computed)"
)


def run(*command: str, env: dict[str, str] | None = None, capture: bool = False, cwd: Path = ROOT) -> str:
    """Run one command and fail on a nonzero exit status."""
    result = subprocess.run(  # noqa: S603 -- argv is fixed or derived from local configuration
        command,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        check=True,
    )
    return result.stdout or ""


def environment() -> dict[str, str]:
    """Provide the development stack's addresses and credentials to the tests.

    The Sync API and the worker run in the development stack's containers. The tests run on
    the host, so they get the worker's environment moved onto the stack's loopback
    publications (`dev.host_environment`), plus the host addresses of Infrahub, the Sync API
    and NetBox. `dev.start` reads the same environment, so the worker container receives the
    NetBox credential from it.
    """
    from tasks import dev  # noqa: PLC0415 -- suite-only dependency
    from tasks.netbox import load_netbox_env, netbox_token, netbox_url  # noqa: PLC0415 -- suite-only dependency

    settings = dev.dev_settings()
    netbox = load_netbox_env()
    principals = json.loads(settings.bearer_tokens)
    env = dict(os.environ)
    env.update(dev.host_environment(settings, "sync-worker"))
    store_url = env[dev.DATABASE_URL_VARIABLE]
    env.update(
        {
            "INFRAHUB_ADDRESS": settings.infrahub_url,
            "INFRAHUB_API_TOKEN": settings.infrahub_admin_token,
            # What a registered package's `infrahub-token` reference resolves.
            dev.INFRAHUB_CREDENTIAL_VARIABLE: settings.infrahub_admin_token,
            "INFRAHUB_SYNC_CONFIG_DIRECTORY": str(ROOT / "examples"),
            "INFRAHUB_SYNC_API_URL": settings.sync_api_url,
            "INFRAHUB_SYNC_API_TOKEN": principals[min(principals)]["token"],
            "NETBOX_URL": netbox_url(netbox),
            "NETBOX_TOKEN": netbox_token(netbox),
            # What the worker resolves for a registered package's `netbox-token`.
            "INFRAHUB_SYNC_CREDENTIAL_NETBOX_TOKEN": netbox_token(netbox),
            "APPLY_GUARD_TEST_POSTGRESQL_DSN": store_url,
            "PRODUCT_STORE_TEST_POSTGRESQL_DSN": store_url,
            "INFRAHUB_SYNC_STORAGE_INTEGRATION_DATABASE_URL": store_url,
            "INFRAHUB_SYNC_STORAGE_INTEGRATION_S3_BUCKET": env["INFRAHUB_SYNC_S3_BUCKET"],
            "INFRAHUB_SYNC_STORAGE_INTEGRATION_S3_ENDPOINT_URL": env[dev.S3_ENDPOINT_VARIABLE],
            "INFRAHUB_SYNC_STORAGE_INTEGRATION_S3_PREFIX": "nightly-e2e",
        }
    )
    return env


# The Compose files the suites start: the development stack, with every profile and the
# Infrahub files it includes, and the local NetBox. A service that the file builds from the
# checkout (the Sync image) is not a third-party image, so the pin check leaves it out.
PINNED_STACKS = (
    (
        ("--profile", "sync", "--profile", "infrahub"),
        ("development/docker-compose.yml",),
    ),
    (
        ("--env-file", "development/netbox/netbox.env"),
        ("development/netbox/docker-compose.netbox.yml",),
    ),
)
# The development stack, with every profile, for its logs.
DEV_STACK_COMMAND = (
    "docker",
    "compose",
    "-f",
    "development/docker-compose.yml",
    "--profile",
    "sync",
    "--profile",
    "infrahub",
)


def third_party_images(resolved: dict[str, Any]) -> list[str]:
    """Return the images of the services a resolved Compose file pulls rather than builds."""
    services: dict[str, dict[str, Any]] = resolved.get("services") or {}
    return sorted(
        {
            str(definition["image"])
            for definition in services.values()
            if definition.get("image") and "build" not in definition
        }
    )


def assert_pinned_images() -> None:
    """Refuse any third-party Compose image without a SHA-256 digest before starting services."""
    for options, files in PINNED_STACKS:
        command = ["docker", "compose", *options]
        for file in files:
            command.extend(("-f", file))
        images = third_party_images(json.loads(run(*command, "config", "--format", "json", capture=True)))
        if not images or any(re.fullmatch(r".+@sha256:[0-9a-f]{64}", image) is None for image in images):
            msg = f"Compose images are missing digest pins in {', '.join(files)}: {images}"
            raise RuntimeError(msg)


def schema_digest(path: Path) -> str:
    """Hash schema paths and content to pin the bundled collection."""
    digest = sha256()
    for file in sorted(path.rglob("*.yml")):
        digest.update(file.relative_to(path).as_posix().encode())
        digest.update(b"\0")
        digest.update(file.read_bytes())
    return digest.hexdigest()


def schema(env: dict[str, str]) -> None:
    """Load the bundled collection required by both NetBox checks."""
    path = ROOT / "tests" / "data" / "nightly_schema"
    if schema_digest(path) != SCHEMA_SHA256:
        msg = "the bundled nightly schema differs from its recorded content digest"
        raise RuntimeError(msg)
    run("uv", "run", "--no-sync", "infrahubctl", "schema", "load", str(path), "--wait", "120", env=env)


def start_stack(env: dict[str, str]) -> None:
    """Start the development stack, allowing the Infrahub health check one cold-start retry."""
    try:
        run("uv", "run", "--no-sync", "invoke", "dev.start", env=env)
    except subprocess.CalledProcessError:
        time.sleep(30)
        run("uv", "run", "--no-sync", "invoke", "dev.start", env=env)


def parsed_fields(output: str) -> dict[str, str]:
    """Read the CLI's named fields without depending on their display order."""
    return dict(line.strip().split(": ", 1) for line in output.splitlines() if ": " in line)


def planned_counts(output: str) -> dict[str, int]:
    """Read operation totals by kind from the saved-plan summary."""
    by_kind = parsed_fields(output).get("by_kind", "")
    counts: dict[str, int] = {}
    for entry in by_kind.split(", "):
        if entry:
            kind, count = entry.split("=", 1)
            counts[kind] = int(count)
    return counts


def check_import_counts(run_id: str, env: dict[str, str]) -> None:
    """Compare branch counts with planned operations, including unexecuted deletes."""
    from infrahub_sdk import Config, InfrahubClientSync  # noqa: PLC0415 -- live-suite dependency

    plan = run("uv", "run", "--no-sync", "infrahub-sync", "runs", "plan", run_id, env=env, capture=True)
    expected = planned_counts(plan)
    if not expected or sum(expected.values()) != EXPECTED_DEMO_OPERATIONS:
        msg = f"the local demo plan has {sum(expected.values())} operations, expected 1688"
        raise RuntimeError(msg)
    client = InfrahubClientSync(config=Config(address=env["INFRAHUB_ADDRESS"], api_token=env["INFRAHUB_API_TOKEN"]))
    actual = {kind: client.count(kind=kind, branch="netbox-import") for kind in expected}
    if actual != expected:
        msg = f"netbox-import object counts differ from the plan: expected {expected}, found {actual}"
        raise RuntimeError(msg)


def from_netbox(env: dict[str, str]) -> None:
    """Register, plan, and apply the shipped example against the local demo dataset."""
    # The worker runs in a container, so the package names NetBox and Infrahub on the
    # networks it joins.
    run("uv", "run", "--no-sync", "invoke", "netbox.demo-package", "--dev-stack", env=env)
    registered = parsed_fields(
        run(
            "uv",
            "run",
            "--no-sync",
            "infrahub-sync",
            "configs",
            "register",
            ".netbox/from-netbox.local.yml",
            "--reason",
            "nightly local NetBox check",
            env=env,
            capture=True,
        )
    )
    config_id = registered["config_id"]
    version = registered["registry_version"]
    run("uv", "run", "--no-sync", "infrahubctl", "branch", "create", "netbox-import", env=env)
    common = ("--config-id", config_id, "--version", version, "--branch", "netbox-import")
    plan_run_id = ""
    for operation in ("plan", "sync"):
        result = run(
            "uv",
            "run",
            "--no-sync",
            "infrahub-sync",
            operation,
            *common,
            "--reason",
            f"nightly local NetBox {operation}",
            "--wait-timeout",
            "1800",
            env=env,
            capture=True,
        )
        fields = parsed_fields(result)
        if operation == "plan" and fields.get("operations") != "1688":
            msg = f"the local demo plan has {fields.get('operations', 'no')} operations, expected 1688"
            raise RuntimeError(msg)
        if operation == "plan":
            plan_run_id = fields["run_id"]
        completed = parsed_fields(
            run("uv", "run", "--no-sync", "infrahub-sync", "runs", "show", fields["run_id"], env=env, capture=True)
        )
        expected_outcome = "planned" if operation == "plan" else "applied"
        if completed.get("outcome") != expected_outcome:
            msg = f"the {operation} run ended with {completed.get('outcome', 'no outcome')}"
            raise RuntimeError(msg)
    check_import_counts(plan_run_id, env)


def plan_operations(output: str) -> list[dict[str, Any]]:
    """Read each operation that `runs plan --detail` prints, with its payload and relationships."""
    operations: list[dict[str, Any]] = []
    lines = output.splitlines()
    # Operations follow the summary counts and the delete disclosure, which can look like one.
    start = next((index + 1 for index, line in enumerate(lines) if line.startswith("by_kind:")), len(lines))
    for line in lines[start:]:
        if _DELETE_DISCLOSURE.match(line):
            continue
        if line.startswith("  ") and operations:
            label, _, value = line.strip().partition(": ")
            if label in {"payload", "relationships"}:
                operations[-1][label] = json.loads(value)
            elif label == "destination id":
                operations[-1]["destination_id"] = value
            continue
        match = _OPERATION_LINE.match(line)
        if match is not None:
            operations.append(
                {
                    "action": match["action"],
                    "kind": match["kind"],
                    "identity": match["identity"] or "",
                }
            )
    return operations


def plan_summary(output: str) -> dict[str, Any]:
    """Read the `operations:` total and whether deletes were computed from the plan summary.

    Only the lines before the operations are read. The total is `None` when it is missing or
    not a number. Deletes count as computed only when the summary says
    `delete_operations_computed: true` and does not print the not-computed disclosure.
    """
    lines = output.splitlines()
    end = next((index for index, line in enumerate(lines) if line.startswith("by_kind:")), len(lines))
    fields = {key.strip(): value.strip() for key, _, value in (line.partition(":") for line in lines[:end])}
    total = fields.get("operations", "")
    disclosed = any(line.startswith("Delete operations were NOT computed") for line in lines)
    return {
        "total": int(total) if total.isdigit() else None,
        "delete_operations_computed": fields.get("delete_operations_computed") == "true" and not disclosed,
    }


def gate_client(env: dict[str, str], context: dict[str, str]) -> Any:  # noqa: ANN401 -- live-suite dependency
    """Return an Infrahub client for the development stack's Infrahub the gate plans against."""
    from infrahub_sdk import Config, InfrahubClientSync  # noqa: PLC0415 -- live-suite dependency

    return InfrahubClientSync(config=Config(address=context["infrahub_address"], api_token=env["INFRAHUB_API_TOKEN"]))


def _json_value(value: object) -> object:
    """Keep a JSON value as it is and write any other value, such as an IP interface, as text."""
    if value is None or isinstance(value, (str, int, float, bool, list, dict)):
        return value
    return str(value)


def current_values(operations: list[dict[str, Any]], client: Any) -> dict[tuple[str, str], dict[str, Any]]:  # noqa: ANN401
    """Read, on the gate branch, the current value of each field an update would change.

    The current value is what v2 wrote. Each updated node is read by its kind and its
    destination id, which `runs plan --detail` prints for every update. A node that has no
    destination id or that cannot be read is left out, and so is a field that is not an
    attribute of the node; `compare_plan` marks what is missing as `NOT_READ`.
    """
    from infrahub_sdk.exceptions import Error as InfrahubError  # noqa: PLC0415 -- live-suite dependency

    values: dict[tuple[str, str], dict[str, Any]] = {}
    for operation in operations:
        destination_id = operation.get("destination_id")
        if operation["action"] != "update" or not destination_id:
            continue
        try:
            node = client.get(kind=operation["kind"], id=destination_id, branch=V2_GATE_BRANCH)
        except InfrahubError:
            continue
        read: dict[str, Any] = {}
        for name in operation.get("payload") or {}:
            attribute = getattr(node, name, None)
            if attribute is not None and hasattr(attribute, "value"):
                read[name] = _json_value(attribute.value)
        values[operation["kind"], operation["identity"]] = read
    return values


def identity_text(identity: object) -> str:
    """Write an identity the way `runs plan --detail` prints it: sorted `key=value` pairs."""
    if isinstance(identity, dict):
        return " ".join(f"{key}={identity[key]}" for key in sorted(identity))
    return str(identity)


def compare_plan(  # noqa: PLR0913 -- the plan detail, its summary and the lists it is compared with
    operations: list[dict[str, Any]],
    expected_deletes: list[dict[str, Any]],
    *,
    total: int | None,
    deletes_computed: bool,
    current: dict[tuple[str, str], dict[str, Any]] | None = None,
    left_out: list[dict[str, Any]] | None = None,
    problems: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Compare the first V3 plan with the data v2 wrote; `None` means a list was not counted.

    `total` is the `operations:` total the plan summary printed and `deletes_computed` whether
    the plan computed deletes. `current` maps the kind and identity of each updated node to
    the current destination values of its fields, which are the values v2 wrote.

    The gate passes only when the plan holds no create, no update, no delete missing from
    the expected-delete list, when records left out and problems were counted and are 0,
    when the detail lists as many operations as the total, and when deletes were computed.
    """
    expected = {(entry["kind"], identity_text(entry["identity"])): entry for entry in expected_deletes}
    counts: dict[str, int | None] = {
        "create": 0,
        "update": 0,
        "delete": 0,
        "left_out": None if left_out is None else len(left_out),
        "problems": None if problems is None else len(problems),
    }
    unexpected: list[dict[str, Any]] = []
    matched: list[dict[str, Any]] = []
    for operation in operations:
        action = operation["action"]
        counts[action] = (counts[action] or 0) + 1
        entry = expected.get((operation["kind"], operation["identity"])) if action == "delete" else None
        if entry is not None:
            if entry not in matched:
                matched.append(entry)
            continue
        finding: dict[str, Any] = {
            "finding": action,
            "kind": operation["kind"],
            "identity": operation["identity"],
        }
        if action == "update":
            # `runs plan --detail` prints only the values V3 would write. The value v2 wrote is
            # the destination's current value, read separately into `current`.
            read = (current or {}).get((operation["kind"], operation["identity"]), {})
            finding["fields"] = [
                {"name": name, "v2": read.get(name, NOT_READ), "v3": value}
                for name, value in sorted((operation.get("payload") or {}).items())
            ]
            if operation.get("relationships"):
                finding["relationships"] = operation["relationships"]
        unexpected.append(finding)
    for label, findings in (("left_out", left_out), ("problem", problems)):
        unexpected.extend(
            {"finding": label, "kind": item.get("kind"), "identity": identity_text(item.get("identity", ""))}
            for item in findings or ()
        )
    complete = total == len(operations) and deletes_computed
    return {
        "counts": counts,
        "unexpected": unexpected,
        "expected_deletes_matched": matched,
        "plan_total": total,
        "operations_listed": len(operations),
        "delete_operations_computed": deletes_computed,
        "passed": complete and left_out is not None and problems is not None and not unexpected,
    }


def gate_failure(result: dict[str, Any]) -> str:
    """Say why the gate did not pass, or return an empty string when it passed."""
    if result["passed"]:
        return ""
    reasons = []
    if result["plan_total"] is None:
        reasons.append("the plan summary reports no operations total")
    elif result["plan_total"] != result["operations_listed"]:
        reasons.append(
            f"the plan summary reports {result['plan_total']} operations but the detail lists "
            f"{result['operations_listed']}"
        )
    if not result["delete_operations_computed"]:
        reasons.append(DELETES_NOT_COMPUTED)
    if result["unexpected"]:
        found: dict[str, int] = {}
        for finding in result["unexpected"]:
            found[finding["finding"]] = found.get(finding["finding"], 0) + 1
        listed = ", ".join(f"{name}={count}" for name, count in found.items())
        reasons.append(f"the first V3 plan holds unexpected entries: {listed}")
    if result["counts"]["left_out"] is None or result["counts"]["problems"] is None:
        reasons.append(UNCOUNTED)
    return "; ".join(reasons)


def expected_deletes() -> list[dict[str, Any]]:
    """Read the reviewed list of deletes that the gate accepts."""
    import yaml  # noqa: PLC0415 -- the summary and log steps run without the project's packages

    entries = yaml.safe_load(EXPECTED_DELETES.read_text(encoding="utf-8")) or []
    for entry in entries:
        if not {"kind", "identity", "reason"} <= set(entry):
            msg = f"{EXPECTED_DELETES.name} entry {entry} needs a kind, an identity and a reason"
            raise ValueError(msg)
    return entries


def gate_context(env: dict[str, str]) -> dict[str, str]:
    """Name the local NetBox, the development stack's Infrahub and the pinned dataset the gate runs against.

    The host addresses serve v2, which runs on the host, and the schema read of
    `configs convert --live-schema`. The worker addresses are what the registered package
    names, because the Sync worker runs in a container on the stack's networks.
    """
    from tasks.netbox import (  # noqa: PLC0415 -- suite-only dependency
        DEMO_SQL_COMMIT,
        DEMO_SQL_SHA256,
        WORKER_INFRAHUB_URL,
        WORKER_NETBOX_URL,
    )

    return {
        "netbox_address": env["NETBOX_URL"],
        "infrahub_address": env["INFRAHUB_ADDRESS"],
        "worker_netbox_address": WORKER_NETBOX_URL,
        "worker_infrahub_address": WORKER_INFRAHUB_URL,
        "infrahub_version": infrahub_version(env),
        "dataset_commit": DEMO_SQL_COMMIT,
        "dataset_sha256": DEMO_SQL_SHA256,
    }


def infrahub_version(env: dict[str, str]) -> str:
    """Return the version the development stack's Infrahub reports, or `unknown`."""
    import httpx  # noqa: PLC0415 -- live-suite dependency
    from infrahub_sdk import Config, InfrahubClientSync  # noqa: PLC0415 -- live-suite dependency
    from infrahub_sdk.exceptions import Error as InfrahubSdkError  # noqa: PLC0415 -- live-suite dependency

    # The version is a label in the result, never a reason to fail the gate.
    try:
        client = InfrahubClientSync(config=Config(address=env["INFRAHUB_ADDRESS"], api_token=env["INFRAHUB_API_TOKEN"]))
        return str(client.get_version())
    except (httpx.HTTPError, InfrahubSdkError, ValueError):
        return "unknown"


def worker_addresses(package: Path, context: dict[str, str]) -> None:
    """Point the registered package at the addresses the containerized Sync worker reaches."""
    import yaml  # noqa: PLC0415 -- the summary and log steps run without the project's packages

    data = yaml.safe_load(package.read_text(encoding="utf-8"))
    data["configuration"]["source"]["settings"]["url"] = context["worker_netbox_address"]
    data["configuration"]["destination"]["settings"]["url"] = context["worker_infrahub_address"]
    package.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def v2_sync(work: Path, env: dict[str, str], context: dict[str, str]) -> Path:
    """Synchronize the v2 NetBox example once with v2 `V2_VERSION`, installed from PyPI in its own environment."""
    import yaml  # noqa: PLC0415 -- the summary and log steps run without the project's packages

    config = yaml.safe_load(V2_EXAMPLE.read_text(encoding="utf-8"))
    config["destination"]["settings"]["branch"] = V2_GATE_BRANCH
    project = work / "project"
    project.mkdir()
    (project / "config.yml").write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    # v2 `generate` reads the Infrahub address from the file only, not from INFRAHUB_ADDRESS.
    # It runs on a copy that names the development stack's Infrahub, so the file that `sync` and the
    # conversion read keeps the example's address and the variables must take precedence.
    generator = work / "generate"
    generator.mkdir()
    config["destination"]["settings"]["url"] = context["infrahub_address"]
    (generator / "config.yml").write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    # Only what v2 and uv need: the repository's environment and settings stay out.
    v2_env = {key: value for key, value in os.environ.items() if key in {"PATH", "HOME", "TMPDIR"}}
    v2_env.update(
        {
            "NETBOX_ADDRESS": context["netbox_address"],
            "NETBOX_TOKEN": env["NETBOX_TOKEN"],
            "INFRAHUB_ADDRESS": context["infrahub_address"],
            "INFRAHUB_API_TOKEN": env["INFRAHUB_API_TOKEN"],
        }
    )
    v2 = ("uvx", "--from", f"infrahub-sync=={V2_VERSION}", "--with", V2_PYNETBOX, "infrahub-sync")
    run(*v2, "generate", "--name", "from-netbox", "--directory", str(generator), env=v2_env, cwd=work)
    for adapter in ("netbox", "infrahub"):
        shutil.copytree(generator / adapter, project / adapter)
    run(*v2, "sync", "--name", "from-netbox", "--directory", str(project), env=v2_env, cwd=work)
    return project


def v3_plan(project: Path, env: dict[str, str], context: dict[str, str]) -> tuple[str, str]:
    """Convert the v2 configuration, register it, plan on the gate branch and read the plan detail."""
    convert_env = {
        **env,
        "NETBOX_ADDRESS": context["netbox_address"],
        "INFRAHUB_ADDRESS": context["infrahub_address"],
    }
    cli = ("uv", "run", "--no-sync", "infrahub-sync")
    # `--live-schema` maps the IP namespace v2 never mapped to `default`, where v2 wrote it.
    run(*cli, "configs", "convert", str(project / "config.yml"), "--live-schema", "--yes", env=convert_env)
    worker_addresses(project / "package.yml", context)
    registered = parsed_fields(
        run(
            *cli,
            "configs",
            "register",
            str(project / "package.yml"),
            "--reason",
            "nightly v2-to-V3 plan gate",
            env=env,
            capture=True,
        )
    )
    planned = parsed_fields(
        run(
            *cli,
            "plan",
            "--config-id",
            registered["config_id"],
            "--version",
            registered["registry_version"],
            "--branch",
            V2_GATE_BRANCH,
            "--reason",
            "nightly v2-to-V3 plan gate",
            "--wait-timeout",
            "1800",
            env=env,
            capture=True,
        )
    )
    run_id = planned["run_id"]
    completed = parsed_fields(run(*cli, "runs", "show", run_id, env=env, capture=True))
    if completed.get("outcome") != "planned":
        msg = f"the gate diff run ended with {completed.get('outcome', 'no outcome')}"
        raise RuntimeError(msg)
    return run_id, run(*cli, "runs", "plan", run_id, "--detail", env=env, capture=True)


def v2_gate(env: dict[str, str], context: dict[str, str]) -> None:
    """Plan from the converted package against the data v2 wrote, and fail on every difference."""
    # A run that fails before the comparison must not leave an earlier run's result to publish.
    (REPORTS / GATE_RESULT).unlink(missing_ok=True)
    run("uv", "run", "--no-sync", "infrahubctl", "branch", "create", V2_GATE_BRANCH, env=env)
    with tempfile.TemporaryDirectory(prefix="v2-gate-") as work:
        project = v2_sync(Path(work), env, context)
        run_id, detail = v3_plan(project, env, context)
    operations = plan_operations(detail)
    summary = plan_summary(detail)
    current = (
        current_values(operations, gate_client(env, context))
        if any(operation["action"] == "update" for operation in operations)
        else {}
    )
    # Pass `left_out=` and `problems=` once the plan summary reports them.
    result = compare_plan(
        operations,
        expected_deletes(),
        total=summary["total"],
        deletes_computed=summary["delete_operations_computed"],
        current=current,
    )
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / GATE_RESULT).write_text(
        json.dumps(
            {
                "v2_version": V2_VERSION,
                "infrahub_version": context["infrahub_version"],
                "dataset": {
                    "name": "demo",
                    "commit": context["dataset_commit"],
                    "sha256": context["dataset_sha256"],
                },
                "run_id": run_id,
                **result,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    failure = gate_failure(result)
    if failure:
        raise RuntimeError(failure)


def demo_suite(name: str, env: dict[str, str]) -> None:
    """Seed the `demo` dataset, start a fresh development stack with the schema, and run one NetBox suite."""
    run("uv", "run", "--no-sync", "invoke", "dev.destroy", env=env)
    run("uv", "run", "--no-sync", "invoke", "netbox.seed", "--dataset", "demo", env=env)
    start_stack(env)
    schema(env)
    if name == "from-netbox":
        from_netbox(env)
    else:
        v2_gate(env, gate_context(env))


def suite(name: str) -> None:
    """Run one suite, retaining its result even if setup or the tests fail."""
    if name not in SUITES:
        msg = f"unknown suite: {name}"
        raise ValueError(msg)
    REPORTS.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    report = REPORTS / f"{name}.xml"
    failure = ""
    try:
        env = environment()
        if name == "integration":
            start_stack(env)
            run("uv", "run", "--no-sync", "invoke", "dev.seed", env=env)
            # The saved-plan case needs its own fresh destination and seed dataset.
            env.pop("NETBOX_URL")
            env.pop("NETBOX_TOKEN")
            run("uv", "run", "--no-sync", "pytest", "-m", "integration", "--junitxml", str(report), env=env)
        elif name == "smoke":
            run("uv", "run", "--no-sync", "invoke", "dev.destroy", env=env)
            start_stack(env)
            run("uv", "run", "--no-sync", "invoke", "dev.seed", env=env)
            run(
                "uv",
                "run",
                "--no-sync",
                "pytest",
                "-m",
                "smoke",
                "tests/dev_stack/smoke",
                "-q",
                "--junitxml",
                str(report),
                env={**env, "INFRAHUB_SYNC_SMOKE_STACK": "dev"},
            )
        elif name == "saved-plan":
            run("uv", "run", "--no-sync", "invoke", "dev.destroy", env=env)
            run("uv", "run", "--no-sync", "invoke", "netbox.seed", env=env)
            start_stack(env)
            schema(env)
            run(
                "uv",
                "run",
                "--no-sync",
                "pytest",
                "-m",
                "integration",
                "tests/integration/test_saved_plan_apply_integration.py",
                "--junitxml",
                str(report),
                env=env,
            )
        else:
            demo_suite(name, env)
    except (OSError, subprocess.CalledProcessError, KeyError, RuntimeError, ValueError) as exc:
        failure = str(exc)
    duration = time.monotonic() - started
    if not report.exists():
        case = ET.Element("testcase", name=name, time=f"{duration:.3f}")
        if failure:
            ET.SubElement(case, "failure", message=failure)
        root = ET.Element("testsuite", name=name, tests="1", failures=str(bool(failure)), time=f"{duration:.3f}")
        root.append(case)
        ET.ElementTree(root).write(report, encoding="unicode", xml_declaration=True)
    (REPORTS / f"{name}.json").write_text(json.dumps({"duration": duration, "failure": failure}), encoding="utf-8")
    if failure:
        collect_logs(name)
        msg = f"{name}: {failure}"
        raise RuntimeError(msg)


def collect_logs(name: str) -> None:
    """Save the development stack's and NetBox's container logs before a later suite resets them."""
    REPORTS.mkdir(parents=True, exist_ok=True)
    path = REPORTS / f"{name}-containers.log"
    netbox = ("docker", "compose", *PINNED_STACKS[1][0], "-f", PINNED_STACKS[1][1][0])
    with path.open("w", encoding="utf-8") as output:
        for command in (DEV_STACK_COMMAND, netbox):
            subprocess.run([*command, "logs", "--no-color"], cwd=ROOT, stdout=output, stderr=output, check=False)  # noqa: S603


def gate_summary() -> list[str]:
    """Describe the v2-to-V3 plan gate result for the run summary, when the gate wrote one."""
    gate = REPORTS / GATE_RESULT
    if not gate.exists():
        return []
    result = json.loads(gate.read_text(encoding="utf-8"))
    counts = ", ".join(f"{key}={'not counted' if value is None else value}" for key, value in result["counts"].items())
    return [
        "",
        f"v2 {result['v2_version']} to V3 plan gate: passed={str(result['passed']).lower()}; {counts}; "
        f"expected deletes matched={len(result['expected_deletes_matched'])}",
    ]


def summarize(sha: str, *, sha_guard: str = "success") -> None:
    """Write per-suite JUnit counts and durations to the GitHub job summary."""
    lines = [
        f"## Nightly end-to-end run for `{sha}`",
        "",
    ]
    if sha_guard != "success":
        lines.extend(("Requested commit refused by the merged-commit SHA guard.", ""))
    lines.extend(("| Suite | Passed | Failed | Skipped | Duration |", "| --- | ---: | ---: | ---: | ---: |"))
    for name in SUITES:
        report = REPORTS / f"{name}.xml"
        if not report.exists():
            lines.append(f"| {name} | 0 | 1 | 0 | not started |")
            continue
        try:
            root = ET.parse(report).getroot()  # noqa: S314 -- this report was generated by local pytest
        except ET.ParseError:
            lines.append(f"| {name} | 0 | 1 | 0 | incomplete report |")
            continue
        cases = list(root.iter("testcase"))
        failed = sum(any(child.tag in {"failure", "error"} for child in case) for case in cases)
        skipped = sum(any(child.tag == "skipped" for child in case) for case in cases)
        state = REPORTS / f"{name}.json"
        result = json.loads(state.read_text(encoding="utf-8")) if state.exists() else {}
        if (not state.exists() or result.get("failure")) and failed == 0:
            failed = 1
        duration = result.get("duration")
        elapsed = f"{duration:.1f}s" if duration is not None else "incomplete run"
        lines.append(f"| {name} | {max(0, len(cases) - failed - skipped)} | {failed} | {skipped} | {elapsed} |")
    lines.extend(gate_summary())
    with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as summary:
        summary.write("\n".join(lines) + "\n")


def logs(name: str) -> None:
    """Preserve evidence when the suite step stopped before writing a report."""
    REPORTS.mkdir(parents=True, exist_ok=True)
    report = REPORTS / f"{name}.xml"
    if not report.exists():
        case = ET.Element("testcase", name=name)
        ET.SubElement(case, "failure", message="suite step failed or timed out")
        root = ET.Element("testsuite", name=name, tests="1", failures="1")
        root.append(case)
        ET.ElementTree(root).write(report, encoding="unicode", xml_declaration=True)
    collect_logs(name)


if __name__ == "__main__":
    match sys.argv[1:]:
        case ["verify-images"]:
            assert_pinned_images()
        case ["suite", name]:
            suite(name)
        case ["logs", name] if name in SUITES:
            logs(name)
        case ["summary", sha]:
            summarize(sha, sha_guard=os.environ.get("NIGHTLY_SHA_GUARD", "success"))
        case _:
            msg = "usage: nightly_e2e.py verify-images | suite NAME | logs NAME | summary SHA"
            raise SystemExit(msg)
