"""Read-only session creation timeline for the failed immediate decode cell.

Never changes RPC ordering, END timing, transfer ownership or cleanup.
Diagnostic logging makes this unsuitable for performance comparisons.
"""
import functools
import os
import sys


def install_creation_observer(service):
    from lmcache.v1.multiprocess.session import SessionManager
    original = SessionManager.get_or_create

    @functools.wraps(original)
    def observed(self, request_id):
        result = original(self, request_id)
        frame = sys._getframe(1)
        service.record('diagnostic_session_access', request_id,
            caller=frame.f_code.co_name, source=frame.f_code.co_filename,
            source_line=frame.f_lineno, session_object=id(result))
        return result

    SessionManager.get_or_create = observed


if __name__ == '__main__':
    from owned_service import install_service
    from generation_service_observer import install_observer
    from lmcache.cli.main import main
    service = install_service(os.environ['CACHEPILOT_OWNED_SERVICE_DIR'])
    install_observer(service)
    install_creation_observer(service)
    sys.argv[0] = 'lmcache'
    sys.exit(main())
