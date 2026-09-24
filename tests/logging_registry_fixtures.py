"""Restore stdlib `logging`'s process-wide registry state around a test.

`logging.Logger.manager.loggerDict` and the level-name mappings
(`logging._levelToName` / `logging._nameToLevel`) are process-global and
outlive any single test's handlers or monkeypatches. A test that calls
`logging.getLogger()` on a new name, or `logging.addLevelName()` on a level
that already had a name, mutates this shared state for the rest of the
interpreter's life unless something puts it back — including a descendant
logger's `.parent` reference, which flips when an ancestor entry turns from a
`PlaceHolder` into a real `Logger` (`logging.Manager._fixupChildren`).

Not a test module: no assertions live here.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def logging_registry_snapshot() -> Iterator[None]:
    """Snapshot and restore `logging`'s process-wide registry state exactly."""
    manager = logging.Logger.manager
    logger_dict_before = dict(manager.loggerDict)
    parents_before = {name: obj.parent for name, obj in logger_dict_before.items() if isinstance(obj, logging.Logger)}
    logger_state_before = {
        name: (list(obj.handlers), obj.level, obj.propagate, obj.disabled)
        for name, obj in logger_dict_before.items()
        if isinstance(obj, logging.Logger)
    }
    level_to_name_before = dict(logging._levelToName)
    name_to_level_before = dict(logging._nameToLevel)
    raise_exceptions_before = logging.raiseExceptions

    try:
        yield
    finally:
        logging.raiseExceptions = raise_exceptions_before

        logging._levelToName.clear()
        logging._levelToName.update(level_to_name_before)
        logging._nameToLevel.clear()
        logging._nameToLevel.update(name_to_level_before)

        manager.loggerDict.clear()
        manager.loggerDict.update(logger_dict_before)
        for name, obj in logger_dict_before.items():
            if isinstance(obj, logging.Logger):
                handlers, level, propagate, disabled = logger_state_before[name]
                obj.handlers = handlers
                obj.level = level
                obj.propagate = propagate
                obj.disabled = disabled
                obj.parent = parents_before[name]
        manager._clear_cache()  # ty: ignore[unresolved-attribute]  # undocumented stdlib method, missing from typeshed's Manager stub
