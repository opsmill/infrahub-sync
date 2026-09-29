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

ROOT = Path(__file__).resolve().parents[2]
REPORTS = ROOT / ".preview" / "nightly-e2e"
SUITES = ("integration", "preview", "saved-plan", "from-netbox")
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
    """Provide the shipped disposable service addresses and credentials to the tests."""
    from tasks.netbox import load_netbox_env, netbox_token, netbox_url  # noqa: PLC0415 -- suite-only dependency
    from tasks.preview import (  # noqa: PLC0415 -- match the preview task's runtime settings
        _runtime_env,  # noqa: PLC2701 -- use the same environment as the preview task
        load_preview_env,
        preview_urls,
    )

    preview = load_preview_env()
    netbox = load_netbox_env()
    urls = preview_urls(preview)
    env = _runtime_env(preview)
    store_url = env["INFRAHUB_SYNC_DATABASE_URL"]
    env.update(
        {
            "INFRAHUB_SYNC_API_URL": urls["sync_api"],
            "INFRAHUB_SYNC_API_TOKEN": json.loads(preview["PREVIEW_BEARER_TOKENS"])["tester@local"]["token"],
            "NETBOX_URL": netbox_url(netbox),
            "NETBOX_TOKEN": netbox_token(netbox),
            "APPLY_GUARD_TEST_POSTGRESQL_DSN": store_url,
            "PRODUCT_STORE_TEST_POSTGRESQL_DSN": store_url,
            "INFRAHUB_SYNC_STORAGE_INTEGRATION_DATABASE_URL": store_url,
            "INFRAHUB_SYNC_STORAGE_INTEGRATION_S3_BUCKET": env["INFRAHUB_SYNC_S3_BUCKET"],
            "INFRAHUB_SYNC_STORAGE_INTEGRATION_S3_ENDPOINT_URL": env["INFRAHUB_SYNC_S3_ENDPOINT_URL"],
            "INFRAHUB_SYNC_STORAGE_INTEGRATION_S3_PREFIX": "nightly-e2e",
        }
    )
    return env


def assert_pinned_images() -> None:
    """Refuse any Compose image without a SHA-256 digest before starting services."""
    definitions = (
        (
            "development/preview.env",
            "development/docker-compose.infrahub.yml",
            "development/docker-compose.preview.yml",
        ),
        ("development/netbox/netbox.env", "development/netbox/docker-compose.netbox.yml"),
    )
    for env_file, *files in definitions:
        command = ["docker", "compose", "--env-file", env_file]
        for file in files:
            command.extend(("-f", file))
        images = run(*command, "config", "--images", capture=True).splitlines()
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


def start_preview(env: dict[str, str]) -> None:
    """Allow the disposable Infrahub health check one cold-start retry."""
    try:
        run("uv", "run", "--no-sync", "invoke", "preview.up", env=env)
    except subprocess.CalledProcessError:
        time.sleep(30)
        run("uv", "run", "--no-sync", "invoke", "preview.up", env=env)


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
    run("uv", "run", "--no-sync", "invoke", "netbox.demo-package", env=env)
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
    for operation in ("diff", "sync"):
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
        if operation == "diff" and fields.get("operations") != "1688":
            msg = f"the local demo plan has {fields.get('operations', 'no')} operations, expected 1688"
            raise RuntimeError(msg)
        if operation == "diff":
            plan_run_id = fields["run_id"]
        completed = parsed_fields(
            run("uv", "run", "--no-sync", "infrahub-sync", "runs", "show", fields["run_id"], env=env, capture=True)
        )
        expected_outcome = "planned" if operation == "diff" else "applied"
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
            start_preview(env)
            run("uv", "run", "--no-sync", "invoke", "preview.seed", env=env)
            # The saved-plan case needs its own fresh destination and seed dataset.
            env.pop("NETBOX_URL")
            env.pop("NETBOX_TOKEN")
            run("uv", "run", "--no-sync", "pytest", "-m", "integration", "--junitxml", str(report), env=env)
        elif name == "preview":
            run("uv", "run", "--no-sync", "invoke", "preview.down", "--volumes", env=env)
            start_preview(env)
            run("uv", "run", "--no-sync", "invoke", "preview.seed", env=env)
            run(
                "uv",
                "run",
                "--no-sync",
                "pytest",
                "-m",
                "preview",
                "tests/preview",
                "-q",
                "--junitxml",
                str(report),
                env=env,
            )
        elif name == "saved-plan":
            run("uv", "run", "--no-sync", "invoke", "preview.down", "--volumes", env=env)
            run("uv", "run", "--no-sync", "invoke", "netbox.seed", env=env)
            start_preview(env)
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
            run("uv", "run", "--no-sync", "invoke", "preview.down", "--volumes", env=env)
            run("uv", "run", "--no-sync", "invoke", "netbox.seed", "--dataset", "demo", env=env)
            start_preview(env)
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
    """Save container and host process logs before a later suite resets the stacks."""
    REPORTS.mkdir(parents=True, exist_ok=True)
    path = REPORTS / f"{name}-containers.log"
    with path.open("w", encoding="utf-8") as output:
        for env_file, *files in (
            (
                "development/preview.env",
                "development/docker-compose.infrahub.yml",
                "development/docker-compose.preview.yml",
            ),
            ("development/netbox/netbox.env", "development/netbox/docker-compose.netbox.yml"),
        ):
            command = ["docker", "compose", "--env-file", env_file]
            for file in files:
                command.extend(("-f", file))
            subprocess.run([*command, "logs", "--no-color"], cwd=ROOT, stdout=output, stderr=output, check=False)  # noqa: S603
        for process in ("sync-api", "prefect-worker"):
            log = ROOT / ".preview" / f"{process}.log"
            if log.exists():
                output.write(f"\n{process}:\n")
                output.write(log.read_text(encoding="utf-8", errors="replace"))


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
