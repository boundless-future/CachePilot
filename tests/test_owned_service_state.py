"""Acquisition ownership used by the real service, without global patching."""
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from owned_service import Owner, ReadLock, ServiceOwnership, WriteLock


@pytest.mark.parametrize("worker_id", [None, 0])
def test_lookup_free_partial_range_keeps_other_reader_and_suffix(tmp_path, worker_id):
    from unittest.mock import Mock
    from lmcache.v1.multiprocess.custom_types import IPCCacheServerKey
    from lmcache.v1.multiprocess.modules.lookup import resolve_prefetched_obj_keys
    from lmcache.v1.distributed.api import AttnWindowDesc
    from lmcache.v1.distributed.error import L1Error
    service = ServiceOwnership(tmp_path)
    ipc = IPCCacheServerKey.from_token_ids("test", 1, worker_id,
        list(range(768)), end=512, request_id="r", num_kv_readers=1)
    ctx = Mock(chunk_size=256)
    ctx.token_hasher.compute_chunk_hashes.return_value = [b'a', b'b']
    ctx.layout_desc_registry.find_attn_desc.return_value = AttnWindowDesc([-1])
    ctx.session_manager.get_or_create.return_value = SimpleNamespace(
        prefetch_hit_chunks=3, prefetch_locked_gids=(0,))
    objects = resolve_prefetched_obj_keys(ctx, ipc, 3, (0,))
    suffix = "suffix"
    owner, other = Owner("r", consumed=True), Owner("other", consumed=True)
    service.jobs["r"] = owner
    locks = {k: ReadLock(service, k, 60000) for k in [*objects, suffix]}
    for reader in (owner, other):
        with service.bind(reader):
            for lock in locks.values():
                lock.lock()
    def finish(keys, read_locks):
        for k in keys:
            for _ in range(read_locks):
                locks[k].unlock()
        return {k: L1Error.SUCCESS for k in keys}
    l1 = SimpleNamespace(_lock=service.gate,
        _objects={k: SimpleNamespace(read_lock=v) for k, v in locks.items()}, finish_read=finish)
    service.storage = SimpleNamespace(_l1_manager=l1)
    service.free_lookup(ctx, ipc)
    service.free_lookup(ctx, ipc)  # Duplicate free cannot touch the other reader.
    assert [r[0] for r in owner.reads] == [suffix]
    assert len(other.reads) == 3 and all(lock.is_locked() for lock in locks.values())
    service.release(owner)
    service.release(other)
    assert not any(lock.is_locked() for lock in locks.values())


def key(sequence):
    return SimpleNamespace(request_id=f"cp1:7:{sequence}:{'a' * 32}:r")


def test_late_lookup_and_dead_client_cannot_reacquire(tmp_path):
    service = ServiceOwnership(tmp_path)
    assert not service.admit(key(1))
    assert service.heartbeat(7)
    assert service.admit(key(2))
    assert not service.admit(key(1))
    service.end(key(3).request_id)
    assert not service.admit(key(3))
    service.clients[7]["dead"] = True
    assert not service.heartbeat(7) and not service.admit(key(4))


def test_bounded_client_tombstones_and_lookup_metadata(tmp_path):
    service = ServiceOwnership(tmp_path)
    for client in range(1, 1025):
        assert service.heartbeat(client)
    assert not service.heartbeat(1025)
    service.jobs.update({str(i): Owner(str(i)) for i in range(256)})
    assert not service.admit(key(1))


def test_original_pin_survives_ttl_and_never_releases_new_reader(tmp_path):
    service = ServiceOwnership(tmp_path)
    lock = ReadLock(service, "object", 20)
    old, new = Owner("old"), Owner("new")
    with service.bind(old):
        lock.lock()
    time.sleep(0.04)
    assert lock.is_locked()  # Live pin, despite expired reservation.
    with service.bind(new):
        lock.lock()
    with service.bind(old):
        lock.unlock()
        with pytest.raises(RuntimeError):
            lock.unlock()
    assert lock.is_locked() and len(new.reads) == 1
    with service.bind(new):
        lock.unlock()
    assert not lock.is_locked()
    rows = [json.loads(r) for r in service.output.read_text().splitlines()]
    released = [r for r in rows if r["event"] == "reservation_released"]
    assert [r["disposition"] for r in released] == ["STALE_EPOCH", "RELEASED"]


def test_writer_no_ttl_and_wrong_owner_cannot_publish(tmp_path):
    service = ServiceOwnership(tmp_path)
    owner, wrong = Owner("io"), Owner("later")
    with service.bind(owner):
        lock = WriteLock(service, "object", True)
    owner.abandoned = True
    with service.bind(wrong), pytest.raises(RuntimeError):
        lock.unlock()
    assert lock.is_locked() and owner.writes["object"] is lock
    with service.bind(owner):
        lock.unlock()
    assert not lock.is_locked()


def test_terminal_before_return_and_duplicate_marker_retire_once(tmp_path):
    from owned_service import TerminalV1
    service = ServiceOwnership(tmp_path)
    payload = TerminalV1(1, "b" * 32, "original", "retrieve")
    owner = Owner("original")
    task = dict(payload=payload, owner=owner, returned=False, seen=False, succeeded=True)
    service.transfers[1] = task
    releases = []
    service.release = lambda o: releases.append(o)
    service.terminal(payload)
    assert task["seen"] and not releases
    task["returned"] = True
    service.terminal(TerminalV1(1, "c" * 32, "original", "retrieve"))
    assert not releases
    service.terminal(payload)
    service.terminal(payload)
    assert releases == [owner] and not service.transfers


def test_shutdown_sees_popped_batch_before_any_task_exists(tmp_path):
    service = ServiceOwnership(tmp_path)
    listener = SimpleNamespace(keys=["a", "b"])
    def pop(value):
        keys, value.keys = value.keys, []
        return keys
    keys = service.pop_store_batch(listener, pop)
    assert not listener.keys and service.store_batches
    seen = []
    service.process_store_batch(None, lambda _, batch: seen.extend(batch), keys)
    assert seen == keys and not service.store_batches
    service.stopped.set()
    with pytest.raises(RuntimeError):
        service.process_store_batch(None, lambda *_: seen.clear(), keys)
    assert seen == keys


def test_duplicate_terminal_does_not_retry_partial_release(tmp_path):
    from owned_service import TerminalV1
    service = ServiceOwnership(tmp_path)
    payload = TerminalV1(1, "b" * 32, "original", "retrieve")
    owner = Owner("original")
    task = dict(payload=payload, owner=owner, returned=True, seen=False, succeeded=True)
    service.transfers[1] = task
    releases = []
    def fail_release(value):
        releases.append(value)
        raise RuntimeError("partial unpin failure")
    service.release = fail_release
    service.terminal(payload)
    service.terminal(payload)
    assert releases == [owner]
    assert owner.error == "RuntimeError('partial unpin failure')"
    assert service.transfers[1] is task


def test_late_terminal_keeps_ambiguous_submission_unresolved(tmp_path):
    from owned_service import TerminalV1
    service = ServiceOwnership(tmp_path)
    payload = TerminalV1(1, "b" * 32, "original", "retrieve")
    owner = Owner("original", error="unknown submission")
    task = dict(payload=payload, owner=owner, returned=True, seen=False, succeeded=False)
    service.transfers[1] = task
    releases = []
    service.release = lambda value: releases.append(value)
    service.terminal(payload)
    assert task["seen"] and not releases and service.transfers[1] is task


def test_partial_store_partition_keeps_all_original_pins(tmp_path):
    service = ServiceOwnership(tmp_path)
    owner = Owner("l2")
    locks = [ReadLock(service, k, 10000) for k in ("a", "b")]
    with service.bind(owner):
        for lock in locks:
            lock.lock()
    task = SimpleNamespace(read_locked_keys=["a", "missing"])
    with pytest.raises(RuntimeError, match="partition failed"):
        service.partition_store_reads(owner, [(1, task)])
    assert owner in service.unresolved and owner.error and task._cachepilot_owner.error
    assert len(owner.reads) == len(task._cachepilot_owner.reads) == 1
    assert all(lock.is_locked() for lock in locks)
