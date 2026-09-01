"""Frozen CLI-to-client matrix and removal closure."""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from typer.testing import CliRunner

from infrahub_sync.cli import app
from infrahub_sync.configuration import ConfigurationPackage

ROOT = Path(__file__).resolve().parents[2]
RUNNER = CliRunner()
_RETIRED_OPTION = (
    r"--(?:directory|config-file|adapter-path|product-cache-location|from-plan|concurrent-load|full-extract|parallel|"
    r"allow-rowcount-drop|continue-on-error|show-progress|allow-destination-change|run-id)"
)
_CLI_INVOCATION = r"(?<![\w.-])(?:uv\s+run\s+)?infrahub-sync\s+(?=(?:configs|runs|diff|sync|apply|generate|list)\b)"
REMOVED_COMMAND = re.compile(
    rf"{_CLI_INVOCATION}(?:generate|list)(?![\w-])"
    rf"|(?<![\w-]){_RETIRED_OPTION}(?![\w-])"
    rf"|{_CLI_INVOCATION}(?:(?!infrahub-sync|```|\n\s*\n)[\s\S])*?(?<![\w-])--(?:name|diff)(?![\w-])"
)

# One row for every CLI consumer in envelope section 12. The HTTP details stay owned by
# tests/client/test_client.py; this matrix closes the command-to-client side of the boundary.
CLI_CLIENT_PARITY = (
    ("configs register PACKAGE", "register_config", "POST /configs"),
    ("configs version CONFIG_ID PACKAGE", "create_config_version", "POST /configs/{config_id}/versions"),
    ("configs list", "list_configs", "GET /configs"),
    ("configs show CONFIG_ID", "get_config", "GET /configs/{config_id}"),
    ("configs show CONFIG_ID --version VERSION", "get_config_version", "GET /configs/{config_id}/versions/{version}"),
    ("configs versions CONFIG_ID", "list_config_versions", "GET /configs/{config_id}/versions"),
    ("configs validate CONFIG_ID VERSION", "validate_config", "POST /configs/{config_id}/versions/{version}/validate"),
    ("diff", "plan", "POST /runs"),
    ("sync", "sync", "POST /runs"),
    ("runs plan RUN_ID", "get_plan", "GET /runs/{run_id}/plan"),
    ("apply RUN_ID", "apply", "POST /runs/{run_id}/apply"),
    ("apply RUN_ID failure evidence", "get_results", "GET /runs/{run_id}/results"),
)


def _help(*args: str) -> str:
    result = RUNNER.invoke(app, [*args, "--help"])
    assert result.exit_code == 0, result.output
    return result.output


def test_parity_matrix_is_exactly_the_accepted_cli_surface() -> None:
    assert [row[1] for row in CLI_CLIENT_PARITY] == [
        "register_config",
        "create_config_version",
        "list_configs",
        "get_config",
        "get_config_version",
        "list_config_versions",
        "validate_config",
        "plan",
        "sync",
        "get_plan",
        "apply",
        "get_results",
    ]


def test_root_help_closes_removed_commands_and_adds_resource_groups() -> None:
    help_text = _help()

    assert "configs" in help_text
    assert "runs" in help_text
    assert "diff" in help_text
    assert "sync" in help_text
    assert "apply" in help_text
    assert "generate" not in help_text
    assert "List all available SYNC projects" not in help_text
    assert "--api-token" not in help_text


def test_all_standalone_only_options_are_absent_from_live_command_help() -> None:
    help_text = "\n".join(
        (
            _help("diff"),
            _help("sync"),
            _help("apply"),
            _help("runs", "plan"),
            _help("configs", "register"),
        )
    )
    removed = {
        "--name",
        "--config-file",
        "--directory",
        "--adapter-path",
        "--product-cache-location",
        "--run-id",
        "--concurrent-load",
        "--full-extract",
        "--parallel",
        "--allow-rowcount-drop",
        "--continue-on-error",
        "--show-progress",
        "--diff",
        "--allow-destination-change",
        "--from-plan",
    }
    assert not (removed & set(help_text.split()))
    assert "--detail" in _help("runs", "plan")
    assert "--kind" in _help("runs", "plan")


def test_cli_imports_only_the_shared_client_boundary() -> None:
    source = (ROOT / "infrahub_sync/cli.py").read_text(encoding="utf-8")

    assert "from infrahub_sync.client import" in source
    for forbidden in (
        "httpx",
        "Authorization",
        "Bearer ",
        "execute_standalone",
        "execute_run",
        "product_store",
        "get_potenda_from_instance",
        "InfrahubClientSync",
    ):
        assert forbidden not in source


def test_only_shared_client_constructs_sync_api_http_requests() -> None:
    constructors: list[Path] = []
    for path in (ROOT / "infrahub_sync").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        if "httpx.Client(" in source or "httpx.AsyncClient(" in source:
            constructors.append(path.relative_to(ROOT))

    assert constructors == [Path("infrahub_sync/client/client.py")]


def test_live_docs_and_examples_close_removed_cli_paths() -> None:
    files = [ROOT / "README.md"]
    files.extend(
        path for path in (ROOT / "docs/docs").rglob("*.mdx") if "release-notes" not in path.relative_to(ROOT).parts
    )
    files.extend((ROOT / "examples").rglob("*.md"))
    text = "\n".join(path.read_text(encoding="utf-8") for path in files)
    removed_options = (
        "--name",
        "--config-file",
        "--directory",
        "--adapter-path",
        "--product-cache-location",
        "--run-id",
        "--concurrent-load",
        "--full-extract",
        "--parallel",
        "--allow-rowcount-drop",
        "--continue-on-error",
        "--show-progress",
        "--allow-destination-change",
        "--from-plan",
    )

    assert not [token for token in removed_options if token in text]

    source_files = []
    for pattern in ("*.yaml", "*.yml", "*.json", "*.py"):
        source_files.extend((ROOT / "examples").rglob(pattern))
    source_text = "\n".join(path.read_text(encoding="utf-8") for path in source_files)

    assert not [token for token in removed_options if token in source_text]


def test_active_sources_do_not_teach_removed_commands() -> None:
    files = [ROOT / "README.md", ROOT / "tests/test_generated_examples.py"]
    files.extend(
        path
        for path in (ROOT / "docs/docs").rglob("*")
        if path.suffix in {".md", ".mdx"} and "release-notes" not in path.relative_to(ROOT).parts
    )
    files.extend(
        path
        for path in (ROOT / "examples").rglob("*")
        if path.suffix in {".json", ".md", ".mdx", ".py", ".yaml", ".yml"}
    )
    files.extend((ROOT / "infrahub_sync/generator/templates").glob("*.j2"))

    offenders = {
        str(path.relative_to(ROOT)): match.group(0)
        for path in files
        if (match := REMOVED_COMMAND.search(path.read_text(encoding="utf-8"))) is not None
    }

    assert offenders == {}


def test_workflows_tasks_and_unreleased_changelog_do_not_invoke_removed_commands() -> None:
    files = list((ROOT / ".github").rglob("*.yml"))
    files.extend((ROOT / ".github").rglob("*.yaml"))
    files.extend((ROOT / "tasks").rglob("*.py"))
    files.extend(path for path in (ROOT / "scripts").rglob("*") if path.is_file())
    files.extend((ROOT / "changelog").glob("*.md"))

    offenders = {
        str(path.relative_to(ROOT)): match.group(0)
        for path in files
        if (match := REMOVED_COMMAND.search(path.read_text(encoding="utf-8"))) is not None
    }

    assert offenders == {}


def test_documented_example_packages_use_and_parse_the_registry_envelope() -> None:
    files = [ROOT / "README.md"]
    files.extend(path for path in (ROOT / "docs/docs").rglob("*.mdx") if "release-notes" not in path.parts)
    files.extend((ROOT / "examples").rglob("*.md"))
    text = "\n".join(path.read_text(encoding="utf-8") for path in files)
    bare_registration = re.compile(
        r"configs register(?:[ \t]*\\)?\s+(?:\"[^\"]*/config\.ya?ml\"|[^\s\\]+/config\.ya?ml)"
    )
    assert bare_registration.search(text) is None

    package_paths = sorted((ROOT / "examples").glob("*/package.yml"))
    assert package_paths
    for package_path in package_paths:
        relative_path = package_path.relative_to(ROOT).as_posix()
        assert relative_path in text
        package_document = yaml.safe_load(package_path.read_text(encoding="utf-8"))
        config_document = yaml.safe_load(package_path.with_name("config.yml").read_text(encoding="utf-8"))
        assert package_document["configuration"] == config_document
        ConfigurationPackage.model_validate(package_document)


def test_configuration_docs_describe_registered_worker_execution() -> None:
    config = " ".join((ROOT / "docs/docs/reference/config.mdx").read_text(encoding="utf-8").split())
    migration = " ".join((ROOT / "docs/docs/migrating-from-netbox-or-nautobot.mdx").read_text(encoding="utf-8").split())

    assert "register it with `infrahub-sync configs register`" in config
    assert "service worker resolves the registered configuration" in config
    assert "loads installed or pre-rendered adapter classes" in config
    assert "builds runtime models from the registered package" not in config
    assert "generated in the same folder" not in config
    assert "registers the `from-netbox` configuration package" in migration
    assert "generates the `from-netbox` sync code" not in migration


def test_netbox_tutorial_starts_and_authenticates_the_service_boundary() -> None:
    text = (ROOT / "docs/docs/tutorials/netbox-demo-to-infrahub.mdx").read_text(encoding="utf-8")

    required = (
        "infrahub-sync[managed]",
        "prefect server start",
        "prefect worker start",
        "infrahub_sync.managed.deploy",
        "infrahub_sync.managed.serve",
        "INFRAHUB_SYNC_MANAGED_BEARER_TOKENS",
        "INFRAHUB_SYNC_API_URL",
        "INFRAHUB_SYNC_API_TOKEN",
        "The worker, not the CLI, reads the NetBox",
    )
    assert not [token for token in required if token not in text]
