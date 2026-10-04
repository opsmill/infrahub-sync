"""The Prefect pin drift check against Infrahub's pyproject."""

from __future__ import annotations

import base64
import json
import subprocess  # noqa: S404 -- the fakes stand in for one gh call
from pathlib import Path

import pytest

pytest.importorskip("tomllib", reason="the drift check runs on Python 3.11 or later")

from tasks import prefect_alignment
from tasks.prefect_alignment import PrefectAlignmentError, compare, prefect_pins

_INFRAHUB = """
[project]
name = "infrahub"
dependencies = ["prefect==3.8.6", "prefect-redis==0.2.15"]
"""


def _sync(*pins: str) -> str:
    extras = ", ".join(f'"{pin}"' for pin in pins)
    return f"""
[project]
name = "infrahub-sync"
dependencies = []

[project.optional-dependencies]
prefect = [{extras}]
"""


def test_the_repository_pins_one_prefect_version() -> None:
    pins = prefect_pins((Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8"))

    assert len(pins) == 1


def test_prefect_redis_is_not_read_as_a_prefect_pin() -> None:
    assert prefect_pins(_INFRAHUB) == {"3.8.6"}


def test_matching_pins_pass() -> None:
    assert (
        compare(_sync("prefect==3.8.6", "prefect==3.8.6; python_version >= '3.11'"), _INFRAHUB, "infrahub-v1.11.4")
        == "3.8.6"
    )


def test_a_different_pin_names_both_versions_and_the_tag() -> None:
    with pytest.raises(PrefectAlignmentError, match=r"3\.8\.1.*infrahub-v1\.11\.4.*3\.8\.6"):
        compare(_sync("prefect==3.8.1"), _INFRAHUB, "infrahub-v1.11.4")


def test_two_different_sync_pins_are_refused() -> None:
    with pytest.raises(PrefectAlignmentError, match="exactly one"):
        compare(_sync("prefect==3.8.6", "prefect==3.8.1"), _INFRAHUB, "infrahub-v1.11.4")


def _answer(payload: object) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["gh"], 0, stdout=json.dumps(payload), stderr="")


def test_the_task_reads_infrahubs_pyproject_at_the_tag_and_reports_the_match(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    content = base64.b64encode(_INFRAHUB.encode()).decode()
    calls: list[list[str]] = []

    def run(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return _answer({"content": content})

    monkeypatch.setattr(prefect_alignment.subprocess, "run", run)

    prefect_alignment.prefect_alignment.body(None, infrahub_tag="infrahub-v1.11.4")

    assert calls == [["gh", "api", "repos/opsmill/infrahub/contents/pyproject.toml?ref=infrahub-v1.11.4"]]
    assert "prefect==3.8.6 matches Infrahub infrahub-v1.11.4" in capsys.readouterr().out


def test_a_failing_gh_is_reported_with_its_own_message(monkeypatch: pytest.MonkeyPatch) -> None:
    def run(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.CalledProcessError(1, argv, stderr="HTTP 404: Not Found\n")

    monkeypatch.setattr(prefect_alignment.subprocess, "run", run)

    with pytest.raises(PrefectAlignmentError, match="HTTP 404: Not Found"):
        prefect_alignment.infrahub_pyproject("infrahub-v9.9.9")


def test_a_missing_gh_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    def run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError

    monkeypatch.setattr(prefect_alignment.subprocess, "run", run)

    with pytest.raises(PrefectAlignmentError, match="gh CLI is not installed"):
        prefect_alignment.latest_infrahub_tag()


def test_an_answer_without_the_field_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(prefect_alignment.subprocess, "run", lambda *_a, **_k: _answer({"message": "Bad credentials"}))

    with pytest.raises(PrefectAlignmentError, match="without 'tag_name'"):
        prefect_alignment.latest_infrahub_tag()


def test_an_infrahub_pyproject_without_a_prefect_pin_says_none() -> None:
    with pytest.raises(PrefectAlignmentError, match="found none"):
        compare(_sync("prefect==3.8.6"), '[project]\nname = "infrahub"\ndependencies = []\n', "infrahub-v1.11.4")


def test_the_repository_pin_is_the_installed_prefect() -> None:
    # Prefect is optional: the base-install leg collects this module without it.
    prefect = pytest.importorskip("prefect")
    pins = prefect_pins((Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8"))

    assert pins == {prefect.__version__}
