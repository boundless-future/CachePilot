"""Diagnostic barriers before real FS lookup/read; never a performance mode.

The controller and filesystem adapter remain real. A bounded asynchronous
barrier changes timing only, before the original coroutine performs its I/O.
"""
import asyncio
import functools
import hashlib
import inspect
import json
import os
from pathlib import Path
import sys
import threading
import time


def install_gate(directory):
    from lmcache.v1.distributed.l2_adapters.fs_l2_adapter import FSL2Adapter

    actual = hashlib.sha256(Path(inspect.getsourcefile(FSL2Adapter)).read_bytes()).hexdigest()
    expected = "b24e98dda60cdf7efce9f175987fd6c7ee7b408c17b7eb2b1da1fb86b0b50bd4"
    if actual != expected:
        raise RuntimeError(f"Unsupported FS adapter source: {actual}")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    output = directory / f"gate-{os.getpid()}.jsonl"

    def record(event, phase, task_id, keys, **fields):
        row = dict(event=event, phase=phase, task_id=task_id,
                   object_keys=[str(key) for key in keys], pid=os.getpid(),
                   unix_time=time.time(), monotonic_ns=time.monotonic_ns(), **fields)
        with output.open("a") as stream:
            stream.write(json.dumps(row) + "\n")

    def wrap(original, phase):
        @functools.wraps(original)
        async def gated(self, keys, *args):
            task_id = args[-1]
            if (directory / "armed").exists():
                record("gate_entered", phase, task_id, keys,
                       paths=[str(self._key_to_path(key)) for key in keys])
                deadline = time.monotonic() + 60
                while not (directory / f"release-{phase}").exists():
                    if time.monotonic() > deadline:
                        # Keep the original I/O alive for safe cleanup, but the
                        # experiment must reject any run with this event.
                        record("gate_timeout", phase, task_id, keys)
                        break
                    await asyncio.sleep(0.02)
                record("gate_released", phase, task_id, keys)
            await original(self, keys, *args)
            record("io_returned", phase, task_id, keys)
        return gated

    for phase in ("lookup", "load"):
        name = f"_execute_{phase}"
        setattr(FSL2Adapter, name, wrap(getattr(FSL2Adapter, name), phase))


def install_shutdown_timeline(directory):
    """Observe close ordering only; no attempt to unlock unfinished I/O."""
    from lmcache.v1.distributed.l2_adapters.fs_l2_adapter import FSL2Adapter
    from lmcache.v1.distributed.storage_manager import StorageManager
    from lmcache.v1.distributed.storage_controllers.prefetch_controller import PrefetchController
    from lmcache.v1.multiprocess.modules.lookup import LookupModule

    output = Path(directory) / f"shutdown-{os.getpid()}.jsonl"

    def record(event, component, **fields):
        with output.open("a") as stream:
            stream.write(json.dumps(dict(event=event, component=component,
                unix_time=time.time(), monotonic_ns=time.monotonic_ns(),
                pid=os.getpid(), **fields)) + "\n")

    for cls, name in ((LookupModule, "close"), (StorageManager, "close"),
                      (PrefetchController, "stop"), (FSL2Adapter, "close")):
        original = getattr(cls, name)

        @functools.wraps(original)
        def observed(self, _original=original, _component=cls.__name__):
            try:
                state = self.report_status()
            except Exception as exc:
                state = dict(snapshot_error=repr(exc))
            record("close_enter", _component, state=state)
            try:
                result = _original(self)
            except BaseException as exc:
                record("close_error", _component, error=repr(exc))
                raise
            # Inspect controller cleanup while the L1 manager is still alive;
            # never query the freed allocator after StorageManager.close().
            after = {}
            if _component in ("LookupModule", "PrefetchController"):
                after["state"] = self.report_status()
            if _component == "PrefetchController":
                after["l1_state"] = self._l1_manager.report_status()
                after["actual_in_flight_entries"] = len(self._in_flight_requests)
            record("close_return", _component, **after)
            return result

        setattr(cls, name, observed)


if __name__ == "__main__":
    from lookup_server_reclaim import check_source, install_reclaim
    from lookup_server_timeline import install_timeline
    check_source()
    directory = os.environ["CACHEPILOT_L2_GATE_DIR"]
    if os.environ.get("CACHEPILOT_OWNED_SERVICE_DIR"):
        from owned_service import install_service
        service = install_service(os.environ["CACHEPILOT_OWNED_SERVICE_DIR"])
        def probe_close():
            trigger = Path(directory) / "probe-close"
            while not trigger.exists():
                time.sleep(0.1)
            try:
                service.lookup.close()
            except RuntimeError as exc:
                service.record("diagnostic_close_refused", error=repr(exc))
                (Path(directory) / "close-refused").touch()
        threading.Thread(target=probe_close, daemon=True).start()
    install_gate(directory)
    install_timeline(directory)
    if os.environ.get("CACHEPILOT_L2_RECLAIM") == "1":
        install_reclaim(directory, checked_release=os.environ.get("CACHEPILOT_CHECKED_RELEASE") == "1")
    if os.environ.get("CACHEPILOT_L2_SHUTDOWN_TIMELINE") == "1":
        install_shutdown_timeline(directory)
    from lmcache.cli.main import main
    sys.argv[0] = "lmcache"
    sys.exit(main())
