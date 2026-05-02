"""
conftest.py — tests/
Registers the parent package in sys.modules before test collection.
Required because the package directory name 'plughub-ai-gateway' contains
a hyphen and cannot be imported as a Python module directly.
"""
import importlib.util
import sys
import types
from pathlib import Path

_PKG_DIR  = Path(__file__).parent.parent          # src/plughub-ai-gateway/
_PKG_NAME = "plughub_ai_gateway"


def _ensure_registered(name: str, directory: Path) -> None:
    if name in sys.modules:
        return
    init = directory / "__init__.py"
    spec = importlib.util.spec_from_file_location(
        name,
        init,
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


# Register the root package and sub-packages before any test is collected
_ensure_registered(_PKG_NAME,                       _PKG_DIR)
_ensure_registered(f"{_PKG_NAME}.tests",            _PKG_DIR / "tests")
_ensure_registered(f"{_PKG_NAME}.providers",        _PKG_DIR / "providers")

# Pre-register this conftest module so pytest's importer doesn't try to
# import it a second time as "plughub_ai_gateway.tests.conftest" and fail.
import types as _types
_conftest_name = f"{_PKG_NAME}.tests.conftest"
if _conftest_name not in sys.modules:
    _self_mod = _types.ModuleType(_conftest_name)
    _self_mod.__file__    = __file__
    _self_mod.__package__ = f"{_PKG_NAME}.tests"
    sys.modules[_conftest_name] = _self_mod
