"""The unit tier runs with the `infrahub-testcontainers` pytest plugin enabled."""

import contextlib
import shlex
import types
from collections.abc import Iterator
from pathlib import Path

import pytest

from tasks import tests as tests_tasks
from tests.conftest import CPU_FREQ_FAILURES, cpu_freq_or_none, patch_cpu_freq

REPO_ROOT = Path(__file__).resolve().parents[1]
# The name `infrahub-testcontainers` registers its pytest plugin under (`pytest11` entry point).
PLUGIN_NAME = "pytest-infrahub-performance-test"


@pytest.mark.parametrize("failure", CPU_FREQ_FAILURES, ids=lambda failure: failure.__name__)
def test_cpu_freq_wrapper_returns_none_when_psutil_raises(failure: type[Exception]) -> None:
    """A psutil failure reading the CPU frequency becomes None, so the plugin's session start survives."""

    def failing_cpu_freq() -> object:
        message = "frequency unavailable"
        raise failure(message)

    assert cpu_freq_or_none(failing_cpu_freq)() is None


def test_cpu_freq_wrapper_passes_through_a_reading() -> None:
    """When psutil reports a frequency, the wrapper returns it unchanged, with the caller's arguments."""
    reading = object()

    def cpu_freq(*, percpu: bool = False) -> object:
        assert percpu
        return reading

    assert cpu_freq_or_none(cpu_freq)(percpu=True) is reading


def test_cpu_freq_wrapper_does_not_hide_other_errors() -> None:
    """An error outside the known psutil failures still raises."""

    def broken_cpu_freq() -> object:
        message = "unexpected"
        raise ValueError(message)

    with pytest.raises(ValueError, match="unexpected"):
        cpu_freq_or_none(broken_cpu_freq)()


def test_patch_cpu_freq_replaces_the_module_function() -> None:
    """After the patch, the module's `cpu_freq` returns None where the original raised."""

    def failing_cpu_freq() -> object:
        message = "frequency unavailable"
        raise SystemError(message)

    module = types.ModuleType("fake_psutil")
    module.__dict__["cpu_freq"] = failing_cpu_freq

    patch_cpu_freq(module)

    assert module.__dict__["cpu_freq"] is not failing_cpu_freq
    assert module.__dict__["cpu_freq"]() is None


class _RecordingContext:
    """Stands in for an invoke `Context`: records each command instead of running it."""

    def __init__(self) -> None:
        self.commands: list[str] = []

    @staticmethod
    @contextlib.contextmanager
    def cd(_path: object) -> Iterator[None]:
        yield

    def run(self, command: str, **_kwargs: object) -> None:
        self.commands.append(command)


@pytest.mark.parametrize("version", [(3, 10), (3, 13)], ids=["py310", "py313"])
def test_the_unit_command_excludes_compose_and_keeps_the_plugin(
    monkeypatch: pytest.MonkeyPatch, version: tuple[int, int]
) -> None:
    """`tests.tests-unit` deselects every live marker and never turns the testcontainers plugin off."""
    monkeypatch.setattr(tests_tasks.sys, "version_info", version)
    context = _RecordingContext()

    tests_tasks.tests_unit.body(context)  # ty: ignore[unresolved-attribute]  # the task body, without the Context type check

    (command,) = context.commands
    marker = shlex.split(command)[shlex.split(command).index("-m") + 1]
    assert {term.strip() for term in marker.split("and")} >= {
        "not integration",
        "not preview",
        "not docker",
        "not compose",
    }
    assert PLUGIN_NAME not in command
    assert "no:" not in command


@pytest.mark.parametrize(
    "path",
    ["tasks/tests.py", "tests/compose/test_fixture_prerequisites.py", "pyproject.toml"],
)
def test_no_pytest_run_disables_the_testcontainers_plugin(path: str) -> None:
    """The plugin stays enabled; `tests/conftest.py` makes its session start survive macOS arm64 instead."""
    assert f"no:{PLUGIN_NAME}" not in (REPO_ROOT / path).read_text(encoding="utf-8")
