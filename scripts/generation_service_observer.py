"""Compatibility entry; implementation lives in cachepilot.telemetry.service."""
import importlib as _importlib
from pathlib import Path as _Path
import sys as _sys

_src = str(_Path(__file__).resolve().parents[1] / "src")
if _src not in _sys.path:
    _sys.path.insert(0, _src)
_impl = _importlib.import_module("cachepilot.telemetry.service")


if __name__ == '__main__':
    from owned_service import install_service
    from lmcache.cli.main import main
    import os
    import sys
    service = install_service(os.environ['CACHEPILOT_OWNED_SERVICE_DIR'])
    _impl.install_observer(service)
    sys.argv[0] = 'lmcache'
    sys.exit(main())

else:
    _sys.modules[__name__] = _impl
