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

import importlib
from collections.abc import Callable
from types import ModuleType
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


def patch_cpu_freq(module: ModuleType) -> None:
    """Replace `module.cpu_freq` with a wrapper that returns None on a known failure.

    The replacement is written into the module's namespace, so the type check does not compare
    it with psutil's own signature, whose return type does not allow None.
    """
    namespace = vars(module)
    namespace["cpu_freq"] = cpu_freq_or_none(namespace["cpu_freq"])


try:
    # Imported by name: the Python 3.10 profile has no psutil, so a static import would not
    # resolve there, while on 3.11+ a suppression for it would be unused.
    psutil = importlib.import_module("psutil")
except ImportError:  # pragma: no cover -- without psutil the plugin is not installed either
    pass
else:
    # psutil's own `cpu_freq` returns None on some platforms too, so callers already handle it.
    patch_cpu_freq(psutil)
