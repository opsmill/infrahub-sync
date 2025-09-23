"""
Plugin loader for InfraHub Sync adapters.

This module is responsible for loading adapter (and optional model) classes
from various sources:

- Built-ins: infrahub_sync.adapters.<name>
- Dotted paths: myproj.adapters.foo:MyAdapter
- Filesystem paths: ./adapters/foo.py:MyAdapter or a package dir
- Python entry points: group infrahub_sync.adapters
"""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import os
import pkgutil
import re
import sys
from enum import Enum
from importlib.metadata import entry_points
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from diffsync import Adapter, DiffSyncModel

if TYPE_CHECKING:
    from collections.abc import Iterable


class PluginLoadError(Exception):
    """Exception raised when a plugin cannot be loaded."""


class Plugintype(str, Enum):
    """Plugin type enum for categorizing how a plugin was loaded."""

    BUILTIN = "builtin"
    DOTTED_PATH = "dotted_path"
    FILESYSTEM = "filesystem"
    ENTRY_POINT = "entry_point"


class PluginLoader:
    """
    Generic plugin loader responsible for resolving adapter classes.

    The loader can resolve classes from:
    - Built-ins: infrahub_sync.adapters.<name>
    - Dotted paths: myproj.adapters.foo:MyAdapter
    - Filesystem paths: ./adapters/foo.py:MyAdapter or a package dir
    - Python entry points: group infrahub_sync.adapters
    """

    def __init__(self, adapter_paths: Iterable[str] | None = None) -> None:
        """
        Initialize a new PluginLoader.

        Args:
            adapter_paths: Optional list of paths to search for adapters.
        """
        self.adapter_paths = list(adapter_paths) if adapter_paths else []
        self._cache: dict[str, tuple[type[Any], Plugintype]] = {}

    @classmethod
    def from_env_and_args(cls, adapter_paths: Iterable[str] | None = None) -> PluginLoader:
        """
        Create a new PluginLoader from environment and arguments.

        This method merges adapter paths from:
        - Environment variable INFRAHUB_SYNC_ADAPTER_PATHS
        - Provided adapter_paths argument

        Args:
            adapter_paths: Optional list of adapter paths from CLI args or config.

        Returns:
            A new PluginLoader instance.
        """
        paths: list[str] = []

        # Add paths from environment variable
        env_paths_str = os.environ.get("INFRAHUB_SYNC_ADAPTER_PATHS", "")
        if env_paths_str:
            # Split by either colon (Unix) or semicolon (Windows)
            separator = ";" if ";" in env_paths_str else ":"
            paths.extend([p.strip() for p in env_paths_str.split(separator) if p.strip()])

        # Add paths from arguments
        if adapter_paths:
            paths.extend(adapter_paths)

        # Make paths absolute and unique while preserving order
        absolute_paths = []
        seen: set[str] = set()
        for path in paths:
            abs_path = str(Path(path).absolute())
            if abs_path not in seen:
                absolute_paths.append(abs_path)
                seen.add(abs_path)

        return cls(absolute_paths)

    def camelize(self, name: str) -> str:
        """
        Convert a name to CamelCase.

        Args:
            name: The name to convert.

        Returns:
            The name in CamelCase.
        """
        # Handle hyphenated names (like "generic-rest-api")
        name = re.sub(r"[-_]", " ", name)
        # Convert to CamelCase
        return "".join(word.capitalize() for word in name.split())

    def resolve(self, spec: str, default_class_candidates: tuple[str, ...] = ("Adapter",)) -> type[Any]:
        """
        Resolve a class from a specification.

        The resolution order is:
        1. Cached result from previous resolution
        2. Explicit dotted pkg.mod[:Class]
        3. Filesystem path.py[:Class] or dir[:Class]
        4. Entry point (group infrahub_sync.adapters, by name)
        5. Built-in infrahub_sync.adapters.<name>

        Args:
            spec: The specification to resolve.
            default_class_candidates: Default class name candidates if not specified.

        Returns:
            The resolved class.

        Raises:
            PluginLoadError: If the class cannot be resolved.
        """
        # Check cache first
        if spec in self._cache:
            return self._cache[spec][0]

        # Parse spec to extract class name if specified
        class_name = None
        if ":" in spec:
            spec_path, class_name = spec.rsplit(":", 1)
        else:
            spec_path = spec

        # Try to resolve by different methods
        cls = None

        is_dotted = (
            "." in spec_path and ("/" not in spec_path and "\\" not in spec_path) and not spec_path.endswith(".py")
        )

        # 1. Dotted path (if it looks like one)
        if is_dotted:
            cls = self._resolve_from_dotted_path(spec_path, class_name, default_class_candidates)
            if cls:
                self._cache[spec] = (cls, Plugintype.DOTTED_PATH)
                return cls

        # 2. Filesystem path (search adapter_paths and CWD)
        cls = self._resolve_from_filesystem(
            path=spec_path, class_name=class_name, default_class_candidates=default_class_candidates
        )
        if cls:
            self._cache[spec] = (cls, Plugintype.FILESYSTEM)
            return cls

        # 3. Try as an entry point
        if cls is None:
            cls = self._resolve_from_entry_point(spec_path, class_name, default_class_candidates)
            if cls:
                self._cache[spec] = (cls, Plugintype.ENTRY_POINT)
                return cls

        # 4. Try as a built-in adapter
        if cls is None:
            cls = self._resolve_from_builtin(spec_path, class_name, default_class_candidates)
            if cls:
                self._cache[spec] = (cls, Plugintype.BUILTIN)
                return cls

        # If we get here, we couldn't resolve the class
        msg = (
            f"Could not resolve adapter class for spec '{spec}'. "
            f"Tried dotted path, filesystem, entry point, and built-in resolution."
        )
        raise PluginLoadError(msg)

    def _resolve_from_dotted_path(
        self, path: str, class_name: str | None, default_class_candidates: tuple[str, ...]
    ) -> type[Any] | None:
        """
        Resolve a class from a dotted path.

        Args:
            path: The dotted path to the module.
            class_name: The name of the class, if specified.
            default_class_candidates: Default class name candidates if not specified.

        Returns:
            The resolved class, or None if not found.
        """
        # Skip paths that look like filesystem paths
        if path.startswith(("./", "/")):
            return None

        try:
            module = importlib.import_module(path)
            return self._find_class_in_module(module, class_name, path, default_class_candidates)
        except (ImportError, AttributeError):
            return None

    def _resolve_from_filesystem(
        self, path: str, class_name: str | None, default_class_candidates: tuple[str, ...]
    ) -> type[Any] | None:
        """
        Resolve a class from a filesystem path.

        Args:
            path: The path to the file or directory.
            class_name: The name of the class, if specified.
            default_class_candidates: Default class name candidates if not specified.

        Returns:
            The resolved class, or None if not found.
        """
        # Handle relative paths (starting with ./)
        cls = None
        if path.startswith("./"):
            # Convert to absolute path based on current directory
            abs_path = Path(path).resolve()
            # If it's a Python file
            if abs_path.exists() and (abs_path.suffix == ".py" or abs_path.with_suffix(".py").exists()):
                file_path = abs_path if abs_path.suffix == ".py" else abs_path.with_suffix(".py")
                module = self._import_from_file(str(file_path))
                if module:
                    cls = self._find_class_in_module(module, class_name, path, default_class_candidates)

            # If it's a directory with __init__.py
            if not cls and abs_path.is_dir() and (abs_path / "__init__.py").exists():
                module = self._import_from_file(str(abs_path / "__init__.py"))
                if module:
                    cls = self._find_class_in_module(module, class_name, path, default_class_candidates)

        # Look in adapter_paths first
        for base_path in self.adapter_paths:
            # Try with just the adapter name (base directory + name)
            full_path = Path(base_path) / path

            # Check if it's a Python file
            if full_path.with_suffix(".py").exists():
                module = self._import_from_file(str(full_path.with_suffix(".py")))
                if module:
                    cls = self._find_class_in_module(module, class_name, path, default_class_candidates)

            # Check if it's a directory with __init__.py
            if not cls and full_path.is_dir() and (full_path / "__init__.py").exists():
                module = self._import_from_file(str(full_path / "__init__.py"))
                if module:
                    cls = self._find_class_in_module(module, class_name, path, default_class_candidates)

        # Try direct path (absolute or relative to current directory)
        path_obj = Path(path)

        # If it's a Python file
        if not cls and path_obj.exists() and path_obj.suffix == ".py":
            module = self._import_from_file(str(path_obj))
            if module:
                cls = self._find_class_in_module(module, class_name, path, default_class_candidates)

        # If it's a directory with __init__.py
        if not cls and path_obj.is_dir() and (path_obj / "__init__.py").exists():
            module = self._import_from_file(str(path_obj / "__init__.py"))
            if module:
                cls = self._find_class_in_module(module, class_name, path, default_class_candidates)

        return cls

    def _resolve_from_entry_point(
        self, name: str, class_name: str | None, default_class_candidates: tuple[str, ...]
    ) -> type[Any] | None:
        """
        Resolve a class from an entry point.

        Args:
            name: The name of the entry point.
            class_name: The name of the class, if specified.
            default_class_candidates: Default class name candidates if not specified.

        Returns:
            The resolved class, or None if not found.
        """
        try:
            # In Python 3.10+, we can use entry_points(group="infrahub_sync.adapters")
            eps = entry_points()
            if hasattr(eps, "select"):  # Python 3.10+
                plugin_entry_points = eps.select(group="infrahub_sync.adapters", name=name)
            else:  # Python < 3.10
                plugin_entry_points = [ep for ep in eps.get("infrahub_sync.adapters", []) if ep.name == name]

            if not plugin_entry_points:
                return None

            # Get the first matching entry point
            ep = next(iter(plugin_entry_points))
            obj = ep.load()

            # If it's a module, find the class
            if inspect.ismodule(obj):
                return self._find_class_in_module(obj, class_name, name, default_class_candidates)
            # If it's already a class, return it
            if inspect.isclass(obj):
                return cast("type[Any]", obj)

        except (ImportError, AttributeError):
            pass

        return None

    def _resolve_from_builtin(
        self, name: str, class_name: str | None, default_class_candidates: tuple[str, ...]
    ) -> type[Any] | None:
        """
        Resolve a class from a built-in adapter.

        Args:
            name: The name of the built-in adapter.
            class_name: The name of the class, if specified.
            default_class_candidates: Default class name candidates if not specified.

        Returns:
            The resolved class, or None if not found.
        """
        # Normalize name by removing hyphens and underscores
        normalized_name = re.sub(r"[-_]", "", name.lower())
        # Try to find a matching module in infrahub_sync.adapters
        try:
            adapters_pkg = importlib.import_module("infrahub_sync.adapters")
        except ImportError:
            return None
        cls = None
        # 1) Exact module
        try:
            module = importlib.import_module(f"infrahub_sync.adapters.{name}")
            cls = self._find_class_in_module(module, class_name, name, default_class_candidates)
            if cls:
                return cls
        except ImportError:
            pass
        # 2) Normalized module
        try:
            module = importlib.import_module(f"infrahub_sync.adapters.{normalized_name}")
            cls = self._find_class_in_module(module, class_name, name, default_class_candidates)
            if cls:
                return cls
        except ImportError:
            pass
        # 3) Iterate package contents
        for _, module_name, _ in pkgutil.iter_modules(adapters_pkg.__path__):
            if normalized_name == re.sub(r"[-_]", "", module_name.lower()):
                module = importlib.import_module(f"infrahub_sync.adapters.{module_name}")
                cls = self._find_class_in_module(module, class_name, name, default_class_candidates)
                if cls:
                    return cls
        return None

    def _import_from_file(self, file_path: str) -> Any | None:
        """
        Import a module from a file path.

        Args:
            file_path: The path to the file.

        Returns:
            The imported module, or None if it couldn't be imported.
        """
        try:
            # Make path absolute to avoid any ambiguity
            abs_path = str(Path(file_path).absolute())

            # Generate a unique module name to avoid conflicts
            module_name = f"infrahub_sync_dynamically_loaded_{abs_path.replace(os.sep, '_').replace('.', '_')}"

            spec = importlib.util.spec_from_file_location(module_name, abs_path)
            if spec is None or spec.loader is None:
                return None

            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)

        except (ImportError, AttributeError, FileNotFoundError):
            return None
        else:
            return module

    def _find_class_in_module(
        self,
        module: Any,
        class_name: str | None,
        name: str,
        default_class_candidates: tuple[str, ...],
    ) -> type[Any] | None:
        """
        Find a class in a module.

        Args:
            module: The module to search.
            class_name: The name of the class, if specified.
            name: The name of the adapter (used for generating candidates).
            default_class_candidates: Default class name candidates if not specified.

        Returns:
            The found class, or None if not found.
        """
        # If class name is specified, look for it directly
        if class_name:
            if hasattr(module, class_name):
                cls = getattr(module, class_name)
                if inspect.isclass(cls):
                    return cls
            return None

        # Get all classes defined in the module
        classes_in_module = [
            obj
            for _, obj in inspect.getmembers(module, inspect.isclass)
            if obj.__module__ == module.__name__ and not issubclass(obj, BaseException)
        ]

        target_base_class = None
        if "Adapter" in default_class_candidates:
            target_base_class = Adapter
        elif "Model" in default_class_candidates:
            target_base_class = DiffSyncModel

        if target_base_class:
            for cls in classes_in_module:
                if issubclass(cls, target_base_class):
                    return cls

        # If we still haven't found it, fall back to name candidates
        return self._find_class_by_name_candidates(module, name, default_class_candidates)

    def _find_class_by_name_candidates(
        self, module: Any, name: str, default_class_candidates: tuple[str, ...]
    ) -> type[Any] | None:
        """Find a class in a module by generating candidate names."""
        # Try to infer class name from adapter name
        base_name = Path(name).stem.replace("_", " ")

        # Generate candidate names
        candidates = []

        # Add camelized name + default suffixes
        camelized = self.camelize(base_name)
        for suffix in default_class_candidates:
            candidates.append(f"{camelized}{suffix}")

        # Add default candidates with appropriate prefix
        for candidate in default_class_candidates:
            candidates.append(f"{camelized}{candidate}")

        # Also look for the default candidates on their own
        candidates.extend(default_class_candidates)

        # Look for any of the candidates
        for candidate in candidates:
            if hasattr(module, candidate):
                cls = getattr(module, candidate)
                if inspect.isclass(cls):
                    return cls

        return None
