"""Compatibility entry; implementation lives in cachepilot.integration.lmcache.guard."""
import importlib as _importlib
from pathlib import Path as _Path
import sys as _sys

_src = str(_Path(__file__).resolve().parents[1] / "src")
if _src not in _sys.path:
    _sys.path.insert(0, _src)
_impl = _importlib.import_module("cachepilot.integration.lmcache.guard")


if __name__ == "__main__":
    from lmcache.cli.main import main
    import os
    import sys

    _impl.install_guard(os.environ["CACHEPILOT_OWNED_MP_DIR"])
    sys.argv[0] = "lmcache"
    sys.exit(main())

else:
    _sys.modules[__name__] = _impl
