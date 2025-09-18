"""Generic plugin loader for adapters and models."""

import importlib
import importlib.util
import os
import sys
from pathlib import Path
from typing import Any, Iterable, Sequence, Type


class PluginLoadError(Exception):
    """Raised when a plugin cannot be loaded."""


class PluginLoader:
    """Generic plugin loader that can resolve adapter classes from various sources."""
    
    def __init__(self, search_paths: Sequence[str] = None):
        """Initialize the plugin loader.
        
        Args:
            search_paths: List of filesystem paths to search for plugins
        """
        self.search_paths = search_paths or []
        self._cache = {}
        
    @classmethod
    def from_env_and_args(cls, adapter_paths: Iterable[str] | None = None) -> "PluginLoader":
        """Create a PluginLoader from environment variables and arguments.
        
        Args:
            adapter_paths: Additional adapter paths from CLI args
            
        Returns:
            Configured PluginLoader instance
        """
        # Get paths from environment variable
        env_paths = []
        if env_var := os.environ.get("INFRAHUB_SYNC_ADAPTER_PATHS"):
            # Split by colon or semicolon for cross-platform support
            separator = ";" if os.name == "nt" else ":"
            env_paths = [p.strip() for p in env_var.split(separator) if p.strip()]
        
        # Combine all paths: ENV + CLI args
        all_paths = env_paths[:]
        if adapter_paths:
            all_paths.extend(adapter_paths)
            
        return cls(search_paths=all_paths)
    
    def resolve(self, spec: str, default_class_candidates: tuple[str, ...] = ("Adapter",)) -> Type[Any]:
        """Resolve a plugin specification to a class.
        
        Args:
            spec: Plugin specification (see resolution order below)
            default_class_candidates: Default class names to try if not specified
            
        Returns:
            The resolved class
            
        Raises:
            PluginLoadError: If the plugin cannot be loaded
            
        Resolution order:
        1. Explicit dotted: pkg.mod[:Class]
        2. Filesystem: path.py[:Class] or dir[:Class] 
        3. Entry point: group 'infrahub_sync.adapters', by name
        4. Built-in: infrahub_sync.adapters.<name> using Adapter or <Name>Adapter
        """
        # Check cache first
        if spec in self._cache:
            return self._cache[spec]
            
        result = None
        error_messages = []
        
        try:
            # 1. Try explicit dotted path (pkg.mod[:Class])
            result = self._try_dotted_import(spec)
            if result:
                self._cache[spec] = result
                return result
        except Exception as e:
            error_messages.append(f"Dotted import: {e}")
        
        try:
            # 2. Try filesystem path (path.py[:Class] or dir[:Class])
            result = self._try_filesystem_import(spec, default_class_candidates)
            if result:
                self._cache[spec] = result
                return result
        except Exception as e:
            error_messages.append(f"Filesystem import: {e}")
            
        try:
            # 3. Try entry points
            result = self._try_entry_point(spec, default_class_candidates)
            if result:
                self._cache[spec] = result
                return result
        except Exception as e:
            error_messages.append(f"Entry point: {e}")
            
        try:
            # 4. Try built-in adapters
            result = self._try_builtin_adapter(spec, default_class_candidates)
            if result:
                self._cache[spec] = result
                return result
        except Exception as e:
            error_messages.append(f"Built-in adapter: {e}")
        
        # If we get here, nothing worked
        raise PluginLoadError(
            f"Could not resolve plugin '{spec}'. Tried:\n" +
            "\n".join(f"  - {msg}" for msg in error_messages)
        )
    
    def _try_dotted_import(self, spec: str) -> Type[Any] | None:
        """Try importing from dotted path like 'myproj.adapters.foo:MyAdapter'."""
        if ":" in spec:
            module_path, class_name = spec.rsplit(":", 1)
        elif "." in spec and not spec.endswith(".py"):
            # Assume it's a module path without explicit class
            module_path = spec
            class_name = None
        else:
            return None
            
        module = importlib.import_module(module_path)
        
        if class_name:
            if not hasattr(module, class_name):
                raise PluginLoadError(f"Class '{class_name}' not found in module '{module_path}'")
            return getattr(module, class_name)
        else:
            # Try to find a suitable class in the module
            for name in dir(module):
                obj = getattr(module, name)
                if isinstance(obj, type) and name.endswith("Adapter"):
                    return obj
            raise PluginLoadError(f"No adapter class found in module '{module_path}'")
    
    def _try_filesystem_import(self, spec: str, default_class_candidates: tuple[str, ...]) -> Type[Any] | None:
        """Try importing from filesystem path like './adapters/foo.py:MyAdapter'."""
        if ":" in spec:
            path_part, class_name = spec.rsplit(":", 1)
        else:
            path_part = spec
            class_name = None
            
        # Check if it looks like a filesystem path
        if not (path_part.startswith((".", "/", "\\")) or 
                path_part.endswith(".py") or
                os.path.exists(path_part)):
            return None
            
        # Resolve the path
        resolved_path = Path(path_part).resolve()
        
        # Try as a Python file
        if resolved_path.suffix == ".py" and resolved_path.is_file():
            return self._import_from_file(resolved_path, class_name, default_class_candidates)
            
        # Try as a directory (package)
        elif resolved_path.is_dir():
            return self._import_from_package(resolved_path, class_name, default_class_candidates)
            
        # Try searching in search paths
        for search_path in self.search_paths:
            search_dir = Path(search_path)
            if not search_dir.is_dir():
                continue
                
            # Try as file in search path
            candidate_file = search_dir / f"{path_part}.py"
            if candidate_file.is_file():
                return self._import_from_file(candidate_file, class_name, default_class_candidates)
                
            # Try as directory in search path  
            candidate_dir = search_dir / path_part
            if candidate_dir.is_dir():
                return self._import_from_package(candidate_dir, class_name, default_class_candidates)
        
        return None
    
    def _import_from_file(self, file_path: Path, class_name: str | None, 
                         default_class_candidates: tuple[str, ...]) -> Type[Any]:
        """Import a class from a Python file."""
        module_name = f"_plugin_{file_path.stem}_{id(file_path)}"
        
        spec = importlib.util.spec_from_file_location(module_name, file_path)
        if not spec or not spec.loader:
            raise PluginLoadError(f"Could not create module spec for {file_path}")
            
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        
        return self._find_class_in_module(module, class_name, default_class_candidates, str(file_path))
    
    def _import_from_package(self, package_path: Path, class_name: str | None,
                            default_class_candidates: tuple[str, ...]) -> Type[Any]:
        """Import a class from a package directory."""
        # Look for __init__.py or adapter.py
        init_file = package_path / "__init__.py"
        adapter_file = package_path / "adapter.py"
        
        target_file = None
        if adapter_file.is_file():
            target_file = adapter_file
        elif init_file.is_file():
            target_file = init_file
        else:
            raise PluginLoadError(f"No __init__.py or adapter.py found in {package_path}")
            
        return self._import_from_file(target_file, class_name, default_class_candidates)
    
    def _find_class_in_module(self, module: Any, class_name: str | None, 
                             default_class_candidates: tuple[str, ...], source: str) -> Type[Any]:
        """Find a class in a module."""
        if class_name:
            if not hasattr(module, class_name):
                raise PluginLoadError(f"Class '{class_name}' not found in {source}")
            return getattr(module, class_name)
        else:
            # Try default candidates
            for candidate in default_class_candidates:
                if hasattr(module, candidate):
                    return getattr(module, candidate)
                    
            # Try camelized name based on file/module name
            if hasattr(module, "__file__"):
                file_stem = Path(module.__file__).stem
                camel_name = self._camelize(file_stem) + "Adapter"
                if hasattr(module, camel_name):
                    return getattr(module, camel_name)
                    
            # Look for any class ending with "Adapter"
            for name in dir(module):
                obj = getattr(module, name)
                if isinstance(obj, type) and name.endswith("Adapter"):
                    return obj
                    
            raise PluginLoadError(f"No suitable adapter class found in {source}")
    
    def _try_entry_point(self, spec: str, default_class_candidates: tuple[str, ...]) -> Type[Any] | None:
        """Try loading from entry points."""
        try:
            from importlib.metadata import entry_points
        except ImportError:
            try:
                from importlib_metadata import entry_points
            except ImportError:
                return None
        
        # Look in the infrahub_sync.adapters group
        eps = entry_points(group="infrahub_sync.adapters")
        if hasattr(eps, "get"):
            # Python 3.10+ style
            ep = eps.get(spec)
        else:
            # Older style
            ep = next((ep for ep in eps if ep.name == spec), None)
            
        if ep:
            return ep.load()
            
        return None
    
    def _try_builtin_adapter(self, spec: str, default_class_candidates: tuple[str, ...]) -> Type[Any] | None:
        """Try loading from built-in adapters."""
        try:
            module = importlib.import_module(f"infrahub_sync.adapters.{spec}")
        except ImportError:
            return None
            
        # Try <Name>Adapter first, then default candidates
        camel_name = self._camelize(spec) + "Adapter"
        candidates = [camel_name] + list(default_class_candidates)
        
        for candidate in candidates:
            if hasattr(module, candidate):
                return getattr(module, candidate)
                
        return None
    
    def _camelize(self, name: str) -> str:
        """Convert snake_case to CamelCase."""
        return "".join(part.capitalize() for part in name.split("_"))


# Global instance for easy access
_default_loader = None


def get_loader(adapter_paths: Iterable[str] | None = None) -> PluginLoader:
    """Get the default plugin loader instance."""
    global _default_loader
    if _default_loader is None:
        _default_loader = PluginLoader.from_env_and_args(adapter_paths)
    return _default_loader


def set_loader(loader: PluginLoader) -> None:
    """Set the default plugin loader instance."""
    global _default_loader
    _default_loader = loader