"""Compatibility entry; implementation lives in cachepilot.runtime.shared_prefix."""
import importlib as _importlib
from pathlib import Path as _Path
import sys as _sys

_src = str(_Path(__file__).resolve().parents[1] / "src")
if _src not in _sys.path:
    _sys.path.insert(0, _src)
_impl = _importlib.import_module("cachepilot.runtime.shared_prefix")

_sys.modules[__name__] = _impl
