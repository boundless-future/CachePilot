"""Isolated FS experiment: real EFBIG via a temporary process file-size limit.

No storage return value is forged. Run only in the smoke's fresh single-client
server; RLIMIT_FSIZE applies process-wide during the one injected coroutine.
"""
import functools
import hashlib
import inspect
import json
import os
from pathlib import Path
import resource
import signal
import sys
import time


def install(directory, disk):
    import aiofiles.os
    from lmcache.v1.distributed.l2_adapters.fs_l2_adapter import FSL2Adapter

    expected = "b24e98dda60cdf7efce9f175987fd6c7ee7b408c17b7eb2b1da1fb86b0b50bd4"
    if hashlib.sha256(Path(inspect.getsourcefile(FSL2Adapter)).read_bytes()).hexdigest() != expected:
        raise RuntimeError("Unsupported FS source")
    directory, disk = Path(directory), Path(disk).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    output = directory / f"write-fault-{os.getpid()}.jsonl"
    signal.signal(signal.SIGXFSZ, signal.SIG_IGN)

    def record(event, **fields):
        with output.open("a") as stream:
            stream.write(json.dumps(dict(event=event, unix_time=time.time(),
                pid=os.getpid(), **fields)) + "\n")

    original_store = FSL2Adapter._execute_store
    original_pop = FSL2Adapter.pop_completed_store_tasks
    original_unlink = aiofiles.os.unlink
    injected = False

    @functools.wraps(original_store)
    async def store(self, keys, objects, task_id):
        nonlocal injected
        if injected or not (directory / "armed").exists():
            return await original_store(self, keys, objects, task_id)
        injected = True
        old_limit = resource.getrlimit(resource.RLIMIT_FSIZE)
        limit = 1024 * 1024
        if old_limit[1] != resource.RLIM_INFINITY and old_limit[1] < limit:
            raise RuntimeError("Unexpected existing file limit")
        record("limit_enter", task_id=task_id, object_count=len(keys),
               expected_object_bytes=[len(obj.byte_array) for obj in objects],
               file_size_limit_bytes=limit)
        resource.setrlimit(resource.RLIMIT_FSIZE, (limit, old_limit[1]))
        try:
            return await original_store(self, keys, objects, task_id)
        finally:
            resource.setrlimit(resource.RLIMIT_FSIZE, old_limit)
            record("limit_restored", task_id=task_id)

    @functools.wraps(original_unlink)
    async def unlink(path, *args, **kwargs):
        target = Path(path).resolve()
        if target.parent == disk and target.suffix == ".tmp":
            record("partial_tmp_before_unlink", filename=target.name,
                   bytes=target.stat().st_size)
        return await original_unlink(path, *args, **kwargs)

    @functools.wraps(original_pop)
    def pop(self):
        completed = original_pop(self)
        for task_id, result in completed.items():
            record("actual_store_result", task_id=task_id,
                   success=result.is_successful(), bytes_transferred=result.bytes_transferred())
        return completed

    FSL2Adapter._execute_store = store
    FSL2Adapter.pop_completed_store_tasks = pop
    aiofiles.os.unlink = unlink


if __name__ == "__main__":
    install(os.environ["CACHEPILOT_WRITE_FAULT_DIR"], os.environ["CACHEPILOT_WRITE_FAULT_DISK"])
    from lmcache.cli.main import main
    sys.argv[0] = "lmcache"
    sys.exit(main())
