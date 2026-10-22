"""The unit tier runs with the `infrahub-testcontainers` pytest plugin enabled."""

import pytest

from tests.conftest import CPU_FREQ_FAILURES, cpu_freq_or_none


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
