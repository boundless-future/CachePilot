"""Compatibility entry; implementation lives in cachepilot.integration.vllm.owned_mp."""
import importlib as _importlib
from pathlib import Path as _Path
import sys as _sys

_src = str(_Path(__file__).resolve().parents[1] / "src")
if _src not in _sys.path:
    _sys.path.insert(0, _src)
_impl = _importlib.import_module("cachepilot.integration.vllm.owned_mp")

def _diagnostic(name):
    if name != "OwnedRejectedRetrieveConnector":
        raise AttributeError(name)
    from scripts.retrieve_failure_connector import ServerRejectedRetrieveConnector
    class OwnedRejectedRetrieveConnector(ServerRejectedRetrieveConnector, _impl.OwnedMPConnector):
        """Legacy one-shot underflow probe; not part of the runtime package."""
    _impl.OwnedRejectedRetrieveConnector = OwnedRejectedRetrieveConnector
    return OwnedRejectedRetrieveConnector

_impl.__getattr__ = _diagnostic

_sys.modules[__name__] = _impl
