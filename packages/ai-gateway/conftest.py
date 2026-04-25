"""
conftest.py — project root
Makes the 'plughub-ai-gateway' source directory importable as 'plughub_ai_gateway'.

The package source lives in 'src/plughub-ai-gateway/' (hyphen makes it
non-importable by Python). This conftest patches pytest's package resolution
to skip the identifier check for this specific directory, then registers the
package in sys.modules so that relative imports resolve correctly.
"""
from __future__ import annotations

import importlib.util
import itertools
import sys
import types
from pathlib import Path

_SRC_DIR  = Path(__file__).parent / "src" / "plughub-ai-gateway"
_PKG_NAME = "plughub_ai_gateway"

# ── 1. Patch pytest's resolve_package_path to handle hyphenated directories ──

import _pytest.pathlib as _pytest_pathlib


def _patched_resolve_package_path(path: Path) -> Path | None:
    """
    Extended version of pytest's resolve_package_path that allows
    'plughub-ai-gateway' (hyphenated) to be treated as a package root,
    mapping it to the importable name 'plughub_ai_gateway'.
    """
    result = None
    for parent in itertools.chain((path,), path.parents):
        if parent.is_dir():
            if not (parent / "__init__.py").is_file():
                break
            # Allow our specific hyphenated directory as if it were an identifier
            name = parent.name
            if not name.isidentifier() and name != "plughub-ai-gateway":
                break
            result = parent
    return result


_pytest_pathlib.resolve_package_path = _patched_resolve_package_path


# ── 2. Patch compute_module_name to map plughub-ai-gateway → plughub_ai_gateway ──

_orig_compute_module_name = _pytest_pathlib.compute_module_name


def _patched_compute_module_name(root: Path, path: Path) -> str | None:
    result = _orig_compute_module_name(root, path)
    if result is None:
        return None
    # Replace hyphens in the package component with underscores
    # e.g. "plughub-ai-gateway.tests.test_inference" → "plughub_ai_gateway.tests.test_inference"
    parts = result.split(".")
    parts = [p.replace("-", "_") for p in parts]
    return ".".join(parts)


_pytest_pathlib.compute_module_name = _patched_compute_module_name


# ── 3. Register all sub-packages in sys.modules ──────────────────────────────

def _register(name: str, directory: Path) -> None:
    if name in sys.modules:
        return
    init = directory / "__init__.py"
    if not init.exists():
        mod = types.ModuleType(name)
        mod.__path__    = [str(directory)]
        mod.__package__ = name
        sys.modules[name] = mod
        return
    spec = importlib.util.spec_from_file_location(
        name, str(init),
        submodule_search_locations=[str(directory)],
    )
    if spec is None or spec.loader is None:
        return
    mod = types.ModuleType(name)
    mod.__spec__    = spec
    mod.__path__    = [str(directory)]
    mod.__package__ = name
    mod.__file__    = str(init)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]


_register(_PKG_NAME,                _SRC_DIR)
_register(f"{_PKG_NAME}.tests",     _SRC_DIR / "tests")
_register(f"{_PKG_NAME}.providers", _SRC_DIR / "providers")
