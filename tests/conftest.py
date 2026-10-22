"""Session-wide test setup that must run before any plugin's session start.

The `infrahub-testcontainers` package registers a pytest plugin that builds a host
profile in `pytest_sessionstart` and calls `psutil.cpu_freq()` without a guard
(`infrahub_testcontainers/host.py`). On macOS arm64 that call fails inside psutil's
macOS backend -- with psutil 7.2 as `SystemError: <built-in function cpu_freq>
returned a result with an exception set`, with older releases as `RuntimeError` --
and pytest stops with an INTERNALERROR before it collects a single test.

The plugin stays enabled. Instead, `psutil.cpu_freq` reports the frequency as
unavailable when it fails: `get_system_stats` already writes `None` for the three
frequency fields when the call returns a false value, and no test reads them.

This conftest sits at the root of `testpaths`, so pytest loads it as an initial
conftest while it parses the configuration, before `pytest_sessionstart` runs. The
patch is therefore in place when the plugin first calls `cpu_freq`.

psutil is only a transitive dependency of `infrahub-testcontainers`; without it the
plugin is absent too, so a missing psutil leaves nothing to patch.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import ParamSpec, TypeVar

P = ParamSpec("P")
R = TypeVar("R")

# The failures psutil raises when the platform cannot report a CPU frequency.
CPU_FREQ_FAILURES: tuple[type[Exception], ...] = (
    RuntimeError,
    SystemError,
    OSError,
    NotImplementedError,
    AttributeError,
)


def cpu_freq_or_none(original: Callable[P, R]) -> Callable[P, R | None]:
    """Wrap `original` so that a known psutil failure returns None instead of raising."""

    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R | None:
        try:
            return original(*args, **kwargs)
        except CPU_FREQ_FAILURES:
            return None

    return wrapper


try:
    # The Python 3.10 profile has no psutil, so its type check cannot resolve it; on 3.11+ the
    # directive is unused and ty reports only a warning.
    import psutil  # ty: ignore[unresolved-import]
except ImportError:  # pragma: no cover -- without psutil the plugin is not installed either
    pass
else:
    # psutil's own `cpu_freq` returns None on some platforms too, so callers already handle it.
    psutil.cpu_freq = cpu_freq_or_none(psutil.cpu_freq)  # ty: ignore[invalid-assignment]
