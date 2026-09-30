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

    def record(event, phase, task_id, keys):
        row = dict(event=event, phase=phase, task_id=task_id,
                   object_keys=[str(key) for key in keys], pid=os.getpid(),
                   unix_time=time.time(), monotonic_ns=time.monotonic_ns())
        with output.open("a") as stream:
            stream.write(json.dumps(row) + "\n")

    def wrap(original, phase):
        @functools.wraps(original)
        async def gated(self, keys, *args):
            task_id = args[-1]
            if (directory / "armed").exists():
                record("gate_entered", phase, task_id, keys)
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


if __name__ == "__main__":
    from lookup_server_reclaim import check_source, install_reclaim
    from lookup_server_timeline import install_timeline
    check_source()
    directory = os.environ["CACHEPILOT_L2_GATE_DIR"]
    install_gate(directory)
    install_timeline(directory)
    if os.environ.get("CACHEPILOT_L2_RECLAIM") == "1":
        install_reclaim(directory)
    from lmcache.cli.main import main
    sys.argv[0] = "lmcache"
    sys.exit(main())
