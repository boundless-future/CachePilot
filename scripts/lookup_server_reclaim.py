"""Opt-in candidate for reclaiming END_SESSION's *unconsumed* MP lookups.

Pinned to the project's LMCache source hash. No installed file is modified.
This is an experimental wrapper, not a general upstream fix: END before LOOKUP,
client death without END, already-consumed results, and a permanently stuck
controller still need separate protocols. Timeout alone never releases locks.
"""

from __future__ import annotations

from dataclasses import dataclass
import functools
import hashlib
import inspect
import json
import logging
import os
from pathlib import Path
import sys
import threading
import time

logger = logging.getLogger("cachepilot.lookup_reclaim")


@dataclass
class Abandoned:
    job: object
    keys: tuple
    readers: int
    found: object = None
    pending_reported: bool = False
    error: str | None = None


class ReclaimState:
    """One owner and one background sweeper per LookupModule instance."""

    def __init__(self, module, directory=None, background=True):
        self.module = module
        self.gate = threading.RLock()
        self.keys = {}  # Active, unconsumed jobs only; removed on query/end.
        self.abandoned = {}  # id(job) -> record holding that exact job alive.
        self.completed = 0
        self.closing = False
        self.wake = threading.Event()
        self.thread = None
        self.output = None
        if directory is not None:
            path = Path(directory)
            path.mkdir(parents=True, exist_ok=True)
            self.output = path / f"reclaim-{os.getpid()}.jsonl"
        if background:
            self.thread = threading.Thread(
                target=self._run, name="cachepilot-prefetch-reclaim", daemon=True
            )
            self.thread.start()

    def record(self, event, request_id, **fields):
        if self.output is None:
            return
        row = dict(event=event, request_id=request_id, pid=os.getpid(),
                   monotonic_ns=time.monotonic_ns(), unix_time=time.time(), **fields)
        with self.output.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, sort_keys=True) + "\n")

    def reap_once(self):
        """Nonblocking poll; called under the gate, including by the worker.

        Store the consumed bitmap before unlock. If an operation raises, retain
        a visible failed entry instead of blindly retrying a possibly partial
        unlock. Recovery requires explicit investigation, not a false success.
        """
        with self.gate:
            storage = self.module._ctx.storage_manager
            for token, entry in list(self.abandoned.items()):
                if entry.error is not None:
                    continue
                request_id = entry.job.request_id
                try:
                    if entry.found is None:
                        entry.found = storage.query_prefetch_status(entry.job.handle)
                    if entry.found is None:
                        if not entry.pending_reported:
                            self.record("reclaim_pending", request_id, token=token)
                            entry.pending_reported = True
                        continue
                    # The storage manager returns the actual retained L1/L2
                    # object bitmap, already trimmed by each storage leg. Do
                    # not rebuild it using a possibly newer session or layout.
                    keys = entry.found.gather(entry.keys)
                    if keys:
                        storage.finish_read_prefetched(keys, read_locks=entry.readers)
                except Exception as exc:
                    entry.error = repr(exc)
                    self.record("reclaim_failed", request_id, token=token, error=entry.error)
                    logger.exception("Prefetch reclaim failed: %s", request_id)
                    continue
                # No process-lifetime tombstones: once ownership is detached,
                # late QUERY/END cannot acquire this job again.
                del self.abandoned[token]
                self.completed += 1
                self.record("reclaim_completed", request_id, token=token,
                            released_objects=len(keys), readers=entry.readers,
                            object_keys=[str(key) for key in keys])

    def _run(self):
        while True:
            self.wake.wait(0.05)
            self.wake.clear()
            with self.gate:
                if self.closing:
                    return
                self.reap_once()

    def close(self):
        with self.gate:
            self.closing = True
        self.wake.set()
        if self.thread is not None:
            self.thread.join(timeout=5)
            if self.thread.is_alive():
                raise RuntimeError("Reclaim worker did not stop")
        self.reap_once()
        if self.abandoned:
            logger.warning("Closing with %d unresolved abandoned jobs", len(self.abandoned))


def install_reclaim(directory=None, *, background=True):
    """Install once before constructing servers; return originals for tests."""
    from lmcache.v1.multiprocess.modules.lookup import LookupModule

    if getattr(LookupModule.lookup, "_cachepilot_reclaim", False):
        raise RuntimeError("Reclaim wrapper already installed")
    names = ("__init__", "lookup", "query_prefetch_status", "query_prefetch_lookup_hits",
             "wait_prefetch_status", "end_session", "close", "report_status")
    original = {name: getattr(LookupModule, name) for name in names}

    @functools.wraps(original["__init__"])
    def initialize(self, *args, **kwargs):
        original["__init__"](self, *args, **kwargs)
        self._cachepilot_reclaim = ReclaimState(self, directory, background)

    @functools.wraps(original["lookup"])
    def lookup(self, key, *args, **kwargs):
        state = self._cachepilot_reclaim
        with state.gate:
            if state.closing:
                raise RuntimeError("Lookup after reclaim shutdown")
            with self._prefetch_job_lock:
                if key.request_id in self._prefetch_jobs:
                    raise RuntimeError("Overlapping unconsumed LOOKUP with same request-id")
            # Capture object identity while this request's layout is available.
            hashes = self._ctx.token_hasher.compute_chunk_hashes(list(key.token_ids), end=key.end)
            attn_desc = self._ctx.layout_desc_registry.find_attn_desc(
                key.model_name, key.world_size)
            group_layouts = self._ctx.layout_desc_registry.find_group_layout_descs(
                key.model_name, key.world_size)
            keys = (tuple(self._chunk_major_object_keys(key, hashes))
                    if attn_desc and group_layouts and hashes else ())
            result = original["lookup"](self, key, *args, **kwargs)
            with self._prefetch_job_lock:
                job = self._prefetch_jobs[key.request_id]
            state.keys[id(job)] = (keys, key.require_num_kv_readers())
            return result

    @functools.wraps(original["query_prefetch_status"])
    def query(self, request_id):
        state = self._cachepilot_reclaim
        with state.gate:
            with self._prefetch_job_lock:
                job = self._prefetch_jobs.get(request_id)
            if job is None:
                return 0
            result = original["query_prefetch_status"](self, request_id)
            if result is not None:
                state.keys.pop(id(job), None)
            return result

    @functools.wraps(original["query_prefetch_lookup_hits"])
    def hits(self, request_id):
        with self._cachepilot_reclaim.gate:
            return original["query_prefetch_lookup_hits"](self, request_id)

    @functools.wraps(original["wait_prefetch_status"])
    def wait(self, request_id, timeout):
        state = self._cachepilot_reclaim
        with state.gate, self._prefetch_job_lock:
            job = self._prefetch_jobs.get(request_id)
        if job is None:
            return 0
        ready = self._ctx.storage_manager.wait_prefetch_status(job.handle, timeout)
        with state.gate:
            with self._prefetch_job_lock:
                if self._prefetch_jobs.get(request_id) is not job:
                    return 0  # Never query the new generation after a long wait.
            return self.query_prefetch_status(request_id) if ready else None

    @functools.wraps(original["end_session"])
    def end(self, request_id):
        state = self._cachepilot_reclaim
        with state.gate:
            with self._prefetch_job_lock:
                job = self._prefetch_jobs.get(request_id)
                if job is not None:
                    keys, readers = state.keys[id(job)]
                    if job.handle.total_requested_keys not in (0, len(keys)):
                        raise RuntimeError("Captured object keys do not match prefetch handle")
                    self._prefetch_jobs.pop(request_id)
                    state.keys.pop(id(job))
                    state.abandoned[id(job)] = Abandoned(job, keys, readers)
            if job is not None:
                state.record("reclaim_owned", request_id, token=id(job),
                             prefetch_request_id=job.handle.prefetch_request_id)
                state.wake.set()
            # The sweeper owns a separate job/key snapshot. END can remove the
            # session without recreating it when the old completion arrives.
            result = original["end_session"](self, request_id)
            state.reap_once()
            return result

    @functools.wraps(original["report_status"])
    def report(self):
        state = self._cachepilot_reclaim
        with state.gate:
            return original["report_status"](self) | dict(
                abandoned_prefetch_jobs=len(state.abandoned),
                reclaim_failed_jobs=sum(x.error is not None for x in state.abandoned.values()),
                reclaim_completed_jobs=state.completed,
                reclaim_key_snapshots=len(state.keys),
            )

    @functools.wraps(original["close"])
    def close(self):
        self._cachepilot_reclaim.close()
        return original["close"](self)

    lookup._cachepilot_reclaim = True
    replacements = dict(__init__=initialize, lookup=lookup, query_prefetch_status=query,
                        query_prefetch_lookup_hits=hits, wait_prefetch_status=wait,
                        end_session=end, report_status=report, close=close)
    for name, method in replacements.items():
        setattr(LookupModule, name, method)
    return original


def check_source():
    from lmcache.v1.multiprocess.modules.lookup import LookupModule
    metadata = json.loads((Path(__file__).resolve().parents[1] /
                           "configs/lookup-reclaim-source.json").read_text())
    path = Path(inspect.getsourcefile(LookupModule))
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != metadata["lookup_sha256"]:
        raise RuntimeError(f"Unsupported lookup.py source: {actual}")
    return actual


if __name__ == "__main__":
    check_source()
    from lookup_server_timeline import install_timeline
    directory = os.environ["CACHEPILOT_LOOKUP_SERVER_TIMELINE_DIR"]
    if os.environ.get("CACHEPILOT_LOOKUP_SERVER_RELEASE_CANCELLED") == "1":
        raise RuntimeError("Cannot combine diagnostic release with candidate reclaim")
    install_timeline(directory)
    install_reclaim(directory)
    from lmcache.cli.main import main
    sys.argv[0] = "lmcache"
    sys.exit(main())
