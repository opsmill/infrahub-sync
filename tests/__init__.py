import builtins

from rich import print as rprint

builtins.rprint = rprint  # ty: ignore[unresolved-attribute]  # injecting rich.print as a test-global helper
