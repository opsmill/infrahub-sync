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
from importlib.metadata import distributions, entry_points
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn, cast

from diffsync import Adapter, DiffSyncModel

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from infrahub_sync import SyncAdapter


class PluginLoadError(Exception):
    """Exception raised when a plugin cannot be loaded."""


# This distribution's own package. Its bundled adapters ship with the product, so they
# are admitted whatever the install style reports — an editable or source checkout has no
# distribution metadata mapping `infrahub_sync` to a distribution at all.
BUNDLED_PACKAGE = "infrahub_sync"
# Where the bundled package really lives, taken from this module's own location rather
# than from `sys.modules`, so a replaced `infrahub_sync` entry cannot move it.
BUNDLED_ROOT = Path(__file__).resolve().parent


def installed_module_origins(spec_path: str) -> set[Path]:
    """Every location an installed distribution ships this exact dotted module at.

    Read from distribution file metadata, so it names the installed copy rather than
    whatever happens to answer the name on ``sys.path``.
    """
    relative = spec_path.replace(".", "/")
    wanted = {f"{relative}.py", f"{relative}/__init__.py"}
    origins: set[Path] = set()
    for distribution in distributions():
        for entry in distribution.files or ():
            if str(entry).replace("\\", "/") in wanted:
                origins.add(Path(str(distribution.locate_file(entry))))
    return origins


def _normalized_module_origin(module: object) -> Path | None:
    """The file a loaded module reports itself as coming from, normalized."""
    spec = getattr(module, "__spec__", None)
    origin = getattr(spec, "origin", None) or getattr(module, "__file__", None)
    if not isinstance(origin, str) or not origin:
        return None
    try:
        return Path(origin).resolve()
    except OSError:
        return None


def module_origin_is_admitted(module: object, spec_path: str) -> bool:
    """Whether the module actually loaded is one registered execution may read.

    Predicting an origin from ``sys.path`` does not cover a name already in
    ``sys.modules`` — ``import_module`` returns that entry without consulting a finder —
    nor a submodule reached through a parent package's manipulated ``__path__``. This
    answers the same provenance question about the module object itself: bundled modules
    must come from inside the installed bundled package, and everything else must be a
    file some installed distribution ships. A module reporting no usable origin is
    refused.
    """
    origin = _normalized_module_origin(module)
    if origin is None:
        return False
    if spec_path.partition(".")[0] == BUNDLED_PACKAGE:
        return origin.is_relative_to(BUNDLED_ROOT)
    return any(candidate.resolve() == origin for candidate in installed_module_origins(spec_path))


def _provides_top_level(base: Path, name: str) -> bool:
    """Whether this import-path entry answers a top-level name at all."""
    return (base / name / "__init__.py").is_file() or (base / f"{name}.py").is_file()


def _module_file(base: Path, parts: Sequence[str]) -> Path | None:
    """The file this import-path entry would supply for a dotted name, or None."""
    current = base
    for index, part in enumerate(parts):
        package_init = current / part / "__init__.py"
        if index == len(parts) - 1:
            # Packages win over same-named modules, as the import system orders them.
            if package_init.is_file():
                return package_init
            module = current / f"{part}.py"
            return module if module.is_file() else None
        if not package_init.is_file():
            return None
        current /= part
    return None


def effective_module_origin(spec_path: str) -> Path | None:
    """The file an import of this dotted name would load, found without importing it.

    Walks ``sys.path`` in order, the way the path finder does. The first entry that
    answers the top-level name decides the answer even when it does not supply the
    submodule, because that entry shadows every later one — which is exactly the case a
    checkout creates over an installed distribution. Anything this cannot resolve — a zip
    import, a namespace package, a custom finder — returns None and is refused.
    """
    parts = spec_path.split(".")
    for entry in sys.path:
        base = Path(entry) if entry else Path.cwd()
        origin = _module_file(base, parts)
        if origin is not None:
            return origin
        if _provides_top_level(base, parts[0]):
            return None
    return None


def is_installed_distribution_module(spec_path: str) -> bool:
    """Whether a dotted target is the module an installed distribution actually ships.

    The provenance rule registered resolution admits by. A dotted spec is admitted when
    its top-level package is this distribution's own, or when the file an import would
    load is exactly a file some installed distribution ships. Owning the top-level name
    is not enough: a checkout module that shadows an installed package answers the name
    from a different file, and is refused.

    Deliberately conservative at one edge: a third-party adapter installed in editable
    mode ships no module files in its metadata either, so it sits outside the registered
    profile until it is installed normally. Answered from distribution metadata and the
    filesystem, so the verdict is reached without importing the candidate.
    """
    if spec_path.partition(".")[0] == BUNDLED_PACKAGE:
        return True
    origins = installed_module_origins(spec_path)
    if not origins:
        return False
    effective = effective_module_origin(spec_path)
    if effective is None:
        return False
    resolved = effective.resolve()
    return any(origin.resolve() == resolved for origin in origins)


def _raise_declared_class_unavailable(
    name: str, class_name: str, default_class_candidates: tuple[str, ...]
) -> NoReturn:
    """Refuse a declaration whose named class the entry point's module cannot supply."""
    target = _target_base_class(default_class_candidates)
    required = "" if target is None else f" as a {target.__name__} subclass"
    msg = f"Entry point '{name}' does not declare a class named '{class_name}'{required}."
    raise PluginLoadError(msg)


def _target_base_class(default_class_candidates: tuple[str, ...]) -> type[Any] | None:
    """The base class a resolution request is really asking for, if it names one."""
    if "Adapter" in default_class_candidates:
        return Adapter
    if "Model" in default_class_candidates:
        return DiffSyncModel
    return None


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

    def __init__(self, adapter_paths: Iterable[str] | None = None, *, installed_only: bool = False) -> None:
        """
        Initialize a new PluginLoader.

        Args:
            adapter_paths: Optional list of paths to search for adapters.
            installed_only: Whether resolution is restricted to installed code. True
                disables filesystem resolution outright and admits a dotted target only
                when :func:`is_installed_distribution_module` owns it.
        """
        self.adapter_paths = list(adapter_paths) if adapter_paths else []
        self.installed_only = installed_only
        self._cache: dict[str, tuple[type[Any], Plugintype]] = {}

    @classmethod
    def installed_only_loader(cls) -> PluginLoader:
        """Return a loader that resolves installed code and nothing else.

        Entry points, bundled adapter modules, and dotted targets an installed
        distribution owns: no configured adapter paths, no
        ``INFRAHUB_SYNC_ADAPTER_PATHS``, no working directory, and no module that is
        importable only because a checkout is on ``sys.path``. This is the loader
        registered execution resolves through, so an adapter that is not installed in
        the worker's environment cannot enter a registered run.
        """
        return cls(adapter_paths=None, installed_only=True)

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
        if is_dotted and (not self.installed_only or is_installed_distribution_module(spec_path)):
            cls = self._resolve_from_dotted_path(spec_path, class_name, default_class_candidates)
            if cls:
                self._cache[spec] = (cls, Plugintype.DOTTED_PATH)
                return cls

        # 2. Filesystem path (search adapter_paths and CWD)
        if not self.installed_only:
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
        if not self.installed_only:
            msg = (
                f"Could not resolve adapter class for spec '{spec}'. "
                f"Tried dotted path, filesystem, entry point, and built-in resolution."
            )
            raise PluginLoadError(msg)
        msg = (
            f"Could not resolve adapter class for spec '{spec}' from installed code. "
            f"Tried entry point and built-in resolution, and dotted import restricted to a "
            f"top-level package owned by an installed distribution."
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
        except (ImportError, AttributeError):
            return None
        # The predicted origin decided whether to import at all; this decides whether the
        # module that answered may be read, which a preloaded or redirected one changes.
        if self.installed_only and not module_origin_is_admitted(module, path):
            return None
        try:
            return self._find_class_in_module(module, class_name, path, default_class_candidates)
        except AttributeError:
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
                resolved = self._find_class_in_module(obj, class_name, name, default_class_candidates)
                if resolved is None and class_name is not None:
                    _raise_declared_class_unavailable(name, class_name, default_class_candidates)
                return resolved
            if inspect.isclass(obj):
                return self._entry_point_class(cast("type[Any]", obj), class_name, name, default_class_candidates)

        except (ImportError, AttributeError):
            pass

        return None

    def _entry_point_class(
        self,
        loaded: type[Any],
        class_name: str | None,
        name: str,
        default_class_candidates: tuple[str, ...],
    ) -> type[Any] | None:
        """Answer a class-valued entry point for whichever class the caller asked for.

        A distribution publishes one entry point per adapter, and it ordinarily names the
        adapter class. That one entry point has to answer both questions the loader asks,
        so a loaded class that is not what was requested is resolved from the module that
        defines it — the same module a module-valued entry point would have named.

        A declaration carrying an explicit ``:ClassName`` is answered by that name and no
        other, because the declared name is what the package checksum covers: resolving
        some other class would let the executed identity differ from the reviewed one.
        """
        target = _target_base_class(default_class_candidates)
        satisfies = target is None or issubclass(loaded, target)
        if satisfies and (class_name is None or loaded.__name__ == class_name):
            return loaded
        defining_module = sys.modules.get(loaded.__module__)
        resolved = (
            None
            if defining_module is None
            else self._find_class_in_module(defining_module, class_name, name, default_class_candidates)
        )
        if resolved is None and class_name is not None:
            _raise_declared_class_unavailable(name, class_name, default_class_candidates)
        return resolved

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
        # If class name is specified, look for it directly. It has to be a class, and it
        # has to be the kind of class the caller asked for: a declaration naming the
        # model where an adapter is required is an error, not a hint to look elsewhere.
        if class_name:
            cls = getattr(module, class_name, None)
            if not inspect.isclass(cls):
                return None
            target = _target_base_class(default_class_candidates)
            if target is not None and not issubclass(cls, target):
                return None
            return cls

        # Get all classes defined in the module
        classes_in_module = [
            obj
            for _, obj in inspect.getmembers(module, inspect.isclass)
            if obj.__module__ == module.__name__ and not issubclass(obj, BaseException)
        ]

        target_base_class = _target_base_class(default_class_candidates)

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


def resolve_installed_adapter_class(adapter: SyncAdapter) -> type[Any]:
    """Resolve one side's adapter class from installed code only.

    The registered worker's resolution seam. It resolves through
    :meth:`PluginLoader.installed_only_loader`, so neither generated Python in the configuration
    directory nor any filesystem plugin — a configured adapter path, the
    ``INFRAHUB_SYNC_ADAPTER_PATHS`` environment, or the working directory — can enter a
    registered run.

    Raises:
        PluginLoadError: no installed class answers the declared adapter.
    """
    return PluginLoader.installed_only_loader().resolve(adapter.adapter or adapter.name)


def resolve_installed_model_base(adapter: SyncAdapter) -> type[Any]:
    """Resolve one side's DiffSync model base from installed code only.

    Uses the spec the generated models file uses — the module half of an explicit
    adapter spec, otherwise the adapter name — so a runtime-built class derives from the
    same base a generated one would have, resolved through the same installed-only loader
    as the adapter class.

    Raises:
        PluginLoadError: no installed class answers the declared adapter.
    """
    spec = adapter.adapter.split(":")[0] if adapter.adapter else adapter.name
    return PluginLoader.installed_only_loader().resolve(spec, default_class_candidates=("Model",))
