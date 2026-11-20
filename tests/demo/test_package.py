"""The NetBox package rewrite replaces only the two shipped addresses."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from infrahub_sync.demo.errors import DemoPackageError
from infrahub_sync.demo.package import (
    SHIPPED_INFRAHUB_URL,
    SHIPPED_NETBOX_URL,
    local_package_text,
    package_name,
)

SHIPPED_PACKAGE = Path(__file__).resolve().parents[2] / "examples" / "netbox_to_infrahub" / "package.yml"
NETBOX_LINE = f'url: "{SHIPPED_NETBOX_URL}"\n'
INFRAHUB_LINE = f'url: "{SHIPPED_INFRAHUB_URL}"\n'


def test_local_package_text_replaces_only_the_two_urls() -> None:
    shipped = SHIPPED_PACKAGE.read_text(encoding="utf-8")

    local = local_package_text(shipped, "http://localhost:8082", "http://localhost:8080")

    shipped_package = yaml.safe_load(shipped)
    local_package = yaml.safe_load(local)
    assert local_package["configuration"]["source"]["settings"]["url"] == "http://localhost:8082"
    assert local_package["configuration"]["destination"]["settings"]["url"] == "http://localhost:8080"
    local_package["configuration"]["source"]["settings"]["url"] = SHIPPED_NETBOX_URL
    local_package["configuration"]["destination"]["settings"]["url"] = SHIPPED_INFRAHUB_URL
    assert local_package == shipped_package
    assert len(local.splitlines()) == len(shipped.splitlines())


def test_local_package_text_accepts_a_netbox_url_equal_to_the_shipped_infrahub_url() -> None:
    """A local NetBox on port 8000 must not make the shipped Infrahub line count twice."""
    shipped = SHIPPED_PACKAGE.read_text(encoding="utf-8")

    local = yaml.safe_load(local_package_text(shipped, SHIPPED_INFRAHUB_URL, "http://localhost:8080"))

    assert local["configuration"]["source"]["settings"]["url"] == SHIPPED_INFRAHUB_URL
    assert local["configuration"]["destination"]["settings"]["url"] == "http://localhost:8080"


@pytest.mark.parametrize(
    ("text", "missing_line", "found"),
    [
        pytest.param(INFRAHUB_LINE, NETBOX_LINE, 0, id="netbox-line-missing"),
        pytest.param(NETBOX_LINE, INFRAHUB_LINE, 0, id="infrahub-line-missing"),
        pytest.param(NETBOX_LINE * 2 + INFRAHUB_LINE, NETBOX_LINE, 2, id="netbox-line-doubled"),
        pytest.param(NETBOX_LINE + INFRAHUB_LINE * 2, INFRAHUB_LINE, 2, id="infrahub-line-doubled"),
    ],
)
def test_local_package_text_refuses_a_package_of_another_shape(text: str, missing_line: str, found: int) -> None:
    expected = f"the package must contain {missing_line.strip()!r} exactly once, found {found}"

    with pytest.raises(DemoPackageError) as raised:
        local_package_text(text, "http://localhost:8082", "http://localhost:8080")

    assert str(raised.value) == expected


def test_local_package_text_names_the_source_in_its_refusal() -> None:
    with pytest.raises(DemoPackageError, match=r"^/some/package\.yml must contain"):
        local_package_text(INFRAHUB_LINE, "http://a", "http://b", source="/some/package.yml")


def test_package_name_reads_the_configuration_name() -> None:
    assert package_name(SHIPPED_PACKAGE.read_text(encoding="utf-8")) == "from-netbox"


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("configuration: {}\n", id="no-name"),
        pytest.param("configuration:\n  name: ''\n", id="empty-name"),
        pytest.param("- a list\n", id="not-a-mapping"),
        pytest.param("configuration: [\n", id="not-yaml"),
    ],
)
def test_package_name_refuses_a_package_without_a_name(text: str) -> None:
    with pytest.raises(DemoPackageError, match=r"configuration\.name"):
        package_name(text)
