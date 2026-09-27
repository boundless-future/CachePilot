"""Run the LMCache server with request-level lookup lifecycle tracing."""

import functools
import json
import os
from pathlib import Path
import sys
import threading
import time


def install_timeline(directory):
    from lmcache.v1.multiprocess.modules.lookup import LookupModule

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    output = directory / f"server-{os.getpid()}.jsonl"
    write_lock = threading.Lock()
    release_cancelled = os.environ.get(
        "CACHEPILOT_LOOKUP_SERVER_RELEASE_CANCELLED") == "1"

    def record(module, event, request_id, **fields):
        with module._prefetch_job_lock:
            job_exists = request_id in module._prefetch_jobs
            job_count = len(module._prefetch_jobs)
        row = dict(event=event, request_id=request_id, pid=os.getpid(),
                   monotonic_ns=time.monotonic_ns(), unix_time=time.time(),
                   job_exists=job_exists, active_prefetch_jobs=job_count,
                   **fields)
        with write_lock, output.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, sort_keys=True) + "\n")

    for name in ("lookup", "_register_prefetch_job", "query_prefetch_status",
                 "end_session", "free_lookup_locks"):
        original = getattr(LookupModule, name)

        @functools.wraps(original)
        def observed(self, argument, *args, _name=name, _original=original,
                     **kwargs):
            request_id = (argument.request_id if _name == "_register_prefetch_job"
                          else argument.request_id if _name in ("lookup", "free_lookup_locks")
                          else argument)
            record(self, "server_before", request_id, method=_name)
            try:
                if _name == "end_session" and release_cancelled:
                    with self._prefetch_job_lock:
                        has_job = request_id in self._prefetch_jobs
                    session = self._ctx.session_manager.get(request_id)
                    if has_job and session is not None and session.lookup_ipc_key is not None:
                        chunks = self.query_prefetch_status(request_id)
                        if chunks is not None:
                            self.free_lookup_locks(session.lookup_ipc_key, 1)
                            record(self, "diagnostic_release", request_id,
                                   hit_chunks=chunks)
                        else:
                            record(self, "diagnostic_release_pending", request_id)
                result = _original(self, argument, *args, **kwargs)
            except BaseException as exc:
                record(self, "server_error", request_id, method=_name,
                       error=repr(exc))
                raise
            record(self, "server_after", request_id, method=_name, result=result)
            return result

        setattr(LookupModule, name, observed)


if __name__ == "__main__":
    install_timeline(os.environ["CACHEPILOT_LOOKUP_SERVER_TIMELINE_DIR"])
    from lmcache.cli.main import main

    sys.argv[0] = "lmcache"
    sys.exit(main())
