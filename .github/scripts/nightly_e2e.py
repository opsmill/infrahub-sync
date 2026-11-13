"""Run and report the disposable, dispatch-only nightly end-to-end suites."""

# ruff: noqa: INP001 -- GitHub Actions runs this script directly.

from __future__ import annotations

import json
import os
import re
import subprocess  # noqa: S404 -- fixed commands run against this checkout's disposable services
import sys
import time
import xml.etree.ElementTree as ET  # noqa: S405 -- only local pytest reports are parsed
from hashlib import sha256
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
REPORTS = ROOT / ".dev" / "nightly-e2e"
SUITES = ("integration", "smoke", "saved-plan", "from-netbox")
EXPECTED_DEMO_OPERATIONS = 1688
SCHEMA_SHA256 = "b74a0ff09cde3aafb1275293be7523f1b377d64073c43aca3ccc14cbe4c5a025"


def run(*command: str, env: dict[str, str] | None = None, capture: bool = False) -> str:
    """Run one command and fail on a nonzero exit status."""
    result = subprocess.run(  # noqa: S603 -- argv is fixed or derived from local configuration
        command,
        cwd=ROOT,
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
            run("uv", "run", "--no-sync", "invoke", "dev.destroy", env=env)
            run("uv", "run", "--no-sync", "invoke", "netbox.seed", "--dataset", "demo", env=env)
            start_stack(env)
            schema(env)
            from_netbox(env)
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
