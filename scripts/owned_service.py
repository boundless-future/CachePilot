"""Opt-in ownership adapter for the pinned MP service, TP=1/full attention.

Uses real constructors, allocator, controllers and native copy kernels. Every
read acquisition captures and pins its native reservation before publication.
Timeout abandons a lookup, never an executor or CUDA buffer. Unresolved work
prevents allocator/context shutdown; restart is the recovery boundary.
"""
from contextlib import contextmanager
from dataclasses import dataclass, field
import functools
import hashlib
import inspect
import json
import os
from pathlib import Path
import secrets
import sys
import threading
import time

import msgspec

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "artifacts/reservation-native"))
from cachepilot_reservation_native import ReservationLock, PinStatus, ReleaseStatus

from owned_service_protocol import parse_id


@dataclass
class Owner:
    request_id: str
    reads: list = field(default_factory=list)
    writes: dict = field(default_factory=dict)
    handle: object = None
    key: object = None
    keys: tuple = ()
    abandoned: bool = False
    consumed: bool = False
    claimed: bool = False
    hit_chunks: int = 0
    error: str | None = None


class ReadLock:
    def __init__(self, service, key, ttl):
        self.service, self.key = service, key
        self.core = ReservationLock(ttl)

    def lock(self):
        owner = self.service.current()
        token, = self.core.acquire()
        status, pin = self.core.pin(token)
        if status != PinStatus.PINNED:
            raise RuntimeError("Cannot pin newly acquired reservation")
        owner.reads.append((self.key, self, token, pin))
        self.service.record("reservation_acquired", owner.request_id,
                            lock_id=token.lock_id, epoch=token.epoch, serial=token.serial)

    def unlock(self):
        owner = self.service.current()
        index = next((i for i, item in enumerate(owner.reads) if item[1] is self), None)
        if index is None:
            raise RuntimeError("Anonymous or duplicate reader release")
        item = owner.reads[index]
        if self.core.unpin(item[3]) != ReleaseStatus.RELEASED:
            owner.error = "Native unpin failed; no automatic retry"
            raise RuntimeError(owner.error)
        # Commit unpin before release. Any later failure is retained and is
        # never retried using a key or a newer reader reservation.
        owner.reads.pop(index)
        status = self.core.release(item[2])
        self.service.record("reservation_released", owner.request_id,
                            lock_id=item[2].lock_id, epoch=item[2].epoch,
                            serial=item[2].serial, disposition=status.name)
        if status not in (ReleaseStatus.RELEASED, ReleaseStatus.STALE_EPOCH):
            owner.error = "Unexpected token disposition: " + status.name
            raise RuntimeError(owner.error)

    def is_locked(self):
        return self.core.is_locked()


class WriteLock:
    """Non-expiring writer ownership; abandoned I/O cannot free its buffer."""
    def __init__(self, service, key, locked=False):
        self.service, self.key, self.owner = service, key, None
        if locked:
            self.lock()

    def lock(self):
        if self.owner is not None:
            raise RuntimeError("Overlapping writer")
        self.owner = self.service.current()
        self.owner.writes[self.key] = self

    def unlock(self):
        owner = self.service.current()
        if owner is not self.owner or owner.writes.get(self.key) is not self:
            raise RuntimeError("Writer completion identity mismatch")
        owner.writes.pop(self.key)
        self.owner = None

    def is_locked(self):
        return self.owner is not None


class TerminalV1(msgspec.Struct, frozen=True):
    sequence: int
    nonce: str
    request_id: str
    kind: str


class ServiceOwnership:
    def __init__(self, directory, *, client_timeout=120, shutdown_timeout=10):
        if client_timeout <= 0 or shutdown_timeout <= 0:
            raise ValueError("Positive timeouts required")
        self.gate = threading.RLock()
        self.local = threading.local()
        self.clients = {}
        self.jobs = {}
        self.controllers = {}
        self.transfers = {}
        self.unresolved = []
        self.deferred_entries = []
        self.store_batches = {}
        self.sequence = 0
        self.lookup = self.storage = self.transfer = None
        self.closing = False
        self.stopped = threading.Event()
        self.client_timeout, self.shutdown_timeout = client_timeout, shutdown_timeout
        self.output = Path(directory) / f"owned-service-{os.getpid()}.jsonl"
        self.output.parent.mkdir(parents=True, exist_ok=True)

    def record(self, event, request_id="", **fields):
        with self.gate, self.output.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(dict(event=event, request_id=request_id,
                monotonic_ns=time.monotonic_ns(), unix_time=time.time(), **fields)) + "\n")

    def current(self):
        owner = getattr(self.local, "owner", None)
        if owner is None or owner.error:
            raise RuntimeError("Missing or unresolved acquisition owner")
        return owner

    def pop_store_batch(self, listener, method):
        with self.gate:
            keys = method(listener)
            if keys:
                if self.stopped.is_set():
                    raise RuntimeError("Store work appeared after the admission seal")
                thread = threading.get_ident()
                if thread in self.store_batches:
                    raise RuntimeError("Previous store batch has not finished submission")
                self.store_batches[thread] = tuple(keys)
            return keys

    def process_store_batch(self, controller, method, keys):
        with self.gate:
            thread = threading.get_ident()
            if self.store_batches.get(thread) != tuple(keys) or self.stopped.is_set():
                raise RuntimeError("Untracked store batch cannot acquire buffers")
            try:
                return method(controller, keys)
            except Exception as exc:
                owner = Owner("l2-batch:" + str(thread), error=repr(exc))
                self.unresolved.append(owner)
                self.record("ownership_unresolved", owner.request_id, error=owner.error)
                raise
            finally:
                self.store_batches.pop(thread)

    def partition_store_reads(self, owner, tasks):
        try:
            for task_key, task in tasks:
                child = Owner(owner.request_id + ":" + str(task_key))
                task._cachepilot_owner = child
                for key in task.read_locked_keys:
                    item = next(r for r in owner.reads if r[0] == key)
                    owner.reads.remove(item)
                    child.reads.append(item)
        except Exception as exc:
            owner.error = "L2 store ownership partition failed: " + repr(exc)
            for _, task in tasks:
                child = getattr(task, "_cachepilot_owner", None)
                if child:
                    child.error = owner.error
            raise RuntimeError(owner.error) from exc
        finally:
            if owner.reads or owner.error:
                owner.error = owner.error or "Unassigned L2 store reservations"
                self.unresolved.append(owner)
                self.record("ownership_unresolved", owner.request_id, error=owner.error)

    @contextmanager
    def bind(self, owner):
        previous = getattr(self.local, "owner", None)
        self.local.owner = owner
        try:
            yield
        finally:
            self.local.owner = previous

    def heartbeat(self, client):
        with self.gate:
            state = self.clients.get(client)
            if state is None:
                # Bounded lifetime metadata. Fail closed instead of evicting a
                # tombstone and admitting an arbitrarily late dead client.
                if len(self.clients) >= 1024 or self.closing:
                    return False
                state = self.clients[client] = dict(high=0, dead=False, seen=time.monotonic())
            if state["dead"]:
                return False
            state["seen"] = time.monotonic()
            return True

    def admit(self, key):
        client, sequence = parse_id(key.request_id)
        state = self.clients.get(client)
        if (self.closing or len(self.jobs) >= 256 or state is None or state["dead"]
                or time.monotonic() - state["seen"] > self.client_timeout
                or sequence <= state["high"]):
            return False
        state["high"] = sequence
        return True

    def release(self, owner, keys=None):
        from lmcache.v1.distributed.error import L1Error
        l1 = self.storage._l1_manager
        selected = set(keys) if keys is not None else {r[0] for r in owner.reads}
        with self.bind(owner):
            for key in selected:
                records = [r for r in owner.reads if r[0] == key]
                if not records:
                    continue
                with l1._lock:
                    entry = l1._objects.get(key)
                    if entry is None or entry.read_lock is not records[0][1]:
                        raise RuntimeError("Pinned original L1 object disappeared")
                result = l1.finish_read([key], read_locks=len(records))
                if result[key] != L1Error.SUCCESS:
                    raise RuntimeError("Owned finish_read failed: " + str(result[key]))

    def collect(self, owner):
        if owner.consumed:
            return True
        if owner.handle is None:
            return False
        with self.bind(owner):
            found = self.storage.query_prefetch_status(owner.handle)
        if found is None:
            return False
        owner.consumed = True
        expected = set(found.gather(owner.keys))
        if {r[0] for r in owner.reads} != expected or len(owner.reads) != len(expected):
            raise RuntimeError("Completion bitmap differs from captured reservations")
        self.record("prefetch_collected", owner.request_id, objects=len(expected))
        return True

    def end(self, request_id):
        client, sequence = parse_id(request_id)
        state = self.clients.get(client)
        if state is not None:
            state["high"] = max(state["high"], sequence)
        owner = self.jobs.get(request_id)
        if owner:
            owner.abandoned = True
            self.record("lookup_abandoned", request_id)

    def sweep(self):
        with self.gate:
            ready = [e for e in self.deferred_entries
                     if not any(t["entry"] is e for t in self.transfers.values())]
            for entry in ready:
                self.deferred_entries.remove(entry)
            if ready:
                self.transfer._cachepilot_release_entries(ready)
            now = time.monotonic()
            for client, state in self.clients.items():
                if not state["dead"] and now - state["seen"] > self.client_timeout:
                    state["dead"] = True
                    self.record("client_expired", client=client)
                    for request_id in tuple(self.jobs):
                        if parse_id(request_id)[0] == client:
                            self.end(request_id)
                            self.lookup._ctx.session_manager.remove(request_id)
            for request_id, owner in tuple(self.jobs.items()):
                if owner.error or not owner.abandoned:
                    continue
                try:
                    if not self.collect(owner):
                        continue
                    self.release(owner)
                    if owner.writes:
                        raise RuntimeError("Terminal prefetch retains writers")
                    self.jobs.pop(request_id)
                    with self.lookup._prefetch_job_lock:
                        self.lookup._prefetch_jobs.pop(request_id, None)
                    self.record("lookup_reclaimed", request_id)
                except Exception as exc:
                    owner.error = repr(exc)
                    self.record("ownership_unresolved", request_id, error=owner.error)

    def run(self):
        while not self.stopped.wait(0.1):
            try:
                self.sweep()
            except Exception as exc:
                with self.gate:
                    owner = Owner("service-reaper", error=repr(exc))
                    self.unresolved.append(owner)
                    self.record("ownership_unresolved", owner.request_id, error=owner.error)
                return

    def terminal(self, payload):
        with self.gate:
            task = self.transfers.get(payload.sequence)
            if task is None or task["payload"] != payload:
                self.record("terminal_rejected", payload.request_id)
                return
            if not task["seen"]:
                self.record("terminal_seen", payload.request_id,
                            sequence=payload.sequence, kind=payload.kind)
            task["seen"] = True
            if not task["returned"]:
                return
            self.retire(task)

    def retire(self, task):
        owner = task["owner"]
        if owner.error:
            return
        try:
            if task["payload"].kind == "retrieve":
                self.release(owner)
            else:
                with self.bind(owner):
                    keys = list(owner.writes)
                    if task["succeeded"]:
                        self.storage.finish_write(keys)
                    elif keys:
                        self.storage._l1_manager.finish_write_and_delete(keys)
                if owner.writes:
                    raise RuntimeError("Native writer terminal left reservations")
            self.transfers.pop(task["payload"].sequence)
            self.record("transfer_retired", owner.request_id,
                sequence=task["payload"].sequence, kind=task["payload"].kind,
                succeeded=task["succeeded"])
        except Exception as exc:
            owner.error = repr(exc)
            self.record("ownership_unresolved", owner.request_id, error=owner.error)

    def status(self):
        store_owners = [t._cachepilot_owner for t in self.storage._store_controller._in_flight_tasks.values()
                        if hasattr(t, "_cachepilot_owner")] if self.storage else []
        return dict(owned_jobs=len(self.jobs), owned_controller_jobs=len(self.controllers),
            owned_transfers=len(self.transfers), owned_clients=len(self.clients),
            owned_deferred_contexts=len(self.deferred_entries),
            owned_store_batches=len(self.store_batches),
            owned_failed=len(self.unresolved) + sum(o.error is not None for o in self.jobs.values()) +
                         sum(t["owner"].error is not None for t in self.transfers.values()) +
                         sum(o.error is not None for o in store_owners),
            owned_read_pins=sum(len(o.reads) for o in self.unresolved) +
                            sum(len(o.reads) for o in self.jobs.values()) +
                            sum(len(t["owner"].reads) for t in self.transfers.values()) +
                            sum(len(o.reads) for o in store_owners))


def install_service(directory):
    from lmcache.v1.distributed.l1_manager import L1Manager
    from lmcache.v1.distributed.storage_manager import StorageManager
    from lmcache.v1.distributed.storage_controllers.prefetch_controller import PrefetchController
    from lmcache.v1.distributed.storage_controllers.store_controller import StoreController, StoreListener
    from lmcache.v1.multiprocess.modules.lookup import LookupModule
    from lmcache.v1.multiprocess.modules.management import ManagementModule
    from lmcache.v1.multiprocess.modules.lmcache_driven_transfer import LMCacheDrivenTransferModule
    import lmcache.v1.multiprocess.modules.lmcache_driven_transfer as transfer_module
    from lmcache.v1.distributed.bitmap_ops.fold import fold_unfold_ranked
    from owned_mp_server import SOURCE_HASHES

    hashes = SOURCE_HASHES | {
        "storage_manager.py": "628136dc664be9f32efc6cc4bb29e62c5e7a7702b8c1a708a1ac14b1fde8a430",
        "prefetch_controller.py": "484609bd8146bcfdfacb35c41af9604bc0173d8434494cc9d016b15267e8b20e",
        "l1_manager.py": "ef9281d51dffc7b9de454d198723845fceb7745918935f738b42a7b1e33a79ad",
        "store_controller.py": "efcd066b400d7122ee0af24f405a1b4f0e16b490fb1a44500cb92bc66c3f1d7a",
        "management.py": "c184087e29eaecb9526a5a61b191a857f3cecc8252be9a86c32a99815de5abc3",
    }
    for cls in (L1Manager, StoreController, ManagementModule, StorageManager,
                PrefetchController, LookupModule, LMCacheDrivenTransferModule):
        source = Path(inspect.getsourcefile(cls))
        if hashlib.sha256(source.read_bytes()).hexdigest() != hashes[source.name]:
            raise RuntimeError("Unsupported service source: " + str(source))
    service = ServiceOwnership(directory,
        client_timeout=float(os.environ.get("CACHEPILOT_CLIENT_TIMEOUT", "120")),
        shutdown_timeout=float(os.environ.get("CACHEPILOT_SHUTDOWN_TIMEOUT", "10")))
    original = {}

    def patch(cls, name, make):
        method = getattr(cls, name)
        original[cls, name] = method
        setattr(cls, name, functools.wraps(method)(make(method)))

    def l1_init(method):
        def initialize(self, *args, **kwargs):
            method(self, *args, **kwargs)
            self._lock = threading.RLock()
        return initialize
    patch(L1Manager, "__init__", l1_init)

    def reserve_write(method):
        def reserve(self, *args, **kwargs):
            with service.gate, self._lock:
                service.current()
                try:
                    return method(self, *args, **kwargs)
                finally:
                    for key, entry in self._objects.items():
                        if not isinstance(entry.read_lock, ReadLock):
                            if entry.read_lock.is_locked():
                                raise RuntimeError("Cannot adopt anonymous readers")
                            entry.read_lock = ReadLock(service, key, int(self._read_ttl_seconds * 1000))
                        if not isinstance(entry.write_lock, WriteLock):
                            entry.write_lock = WriteLock(service, key, entry.write_lock.is_locked())
        return reserve
    patch(L1Manager, "reserve_write", reserve_write)

    for name in ("delete", "clear"):
        def no_force(method, name=name):
            def invoke(self, *args, **kwargs):
                position = 1 if name == "delete" else 0
                if kwargs.get("force", False) or (len(args) > position and args[position]):
                    raise RuntimeError("Forced allocator release forbidden in owned service")
                return method(self, *args, **kwargs)
            return invoke
        patch(L1Manager, name, no_force)

    def storage_init(method):
        def initialize(self, *args, **kwargs):
            service.storage = self
            method(self, *args, **kwargs)
            for adapter in self._l2_adapters.values():
                submit = adapter.submit_store_task

                def guarded_submit(*a, _submit=submit, **kw):
                    owner = service.current()
                    try:
                        return _submit(*a, **kw)
                    except Exception as exc:
                        # A submit exception does not establish that an
                        # asynchronous consumer never started.
                        owner.error = "Ambiguous L2 submission: " + repr(exc)
                        service.record("ownership_unresolved", owner.request_id, error=owner.error)
                        raise
                adapter.submit_store_task = guarded_submit
        return initialize
    patch(StorageManager, "__init__", storage_init)

    def submit_prefetch(method):
        def invoke(self, spec, *args, **kwargs):
            from lmcache.v1.distributed.api import PrefetchMode, TrimPolicy
            if (spec.mode is not PrefetchMode.LOOKUP or spec.policy is not TrimPolicy.PREFIX
                    or spec.num_kv_readers != 1):
                raise ValueError("Owned service requires one-reader PREFIX LOOKUP")
            owner = service.current()
            owner.keys = tuple(spec.keys)
            owner.handle = method(self, spec, *args, **kwargs)
            return owner.handle
        return invoke
    patch(StorageManager, "submit_prefetch_task", submit_prefetch)

    def controller_submit(method):
        def invoke(self, spec):
            with service.gate:
                owner = service.current()
                request_id = method(self, spec)
                service.controllers[request_id] = owner
                return request_id
        return invoke
    patch(PrefetchController, "submit_prefetch_request", controller_submit)

    for name in ("_start_lookup_phase", "_advance_request"):
        def controller_scope(method):
            def invoke(self, request, *args, **kwargs):
                request_id = request if isinstance(request, int) else request.request_id
                with service.gate, service.bind(service.controllers[request_id]):
                    owner = service.current()
                    try:
                        return method(self, request, *args, **kwargs)
                    except Exception as exc:
                        owner.error = repr(exc)
                        service.record("ownership_unresolved", owner.request_id, error=owner.error)
                        raise
            return invoke
        patch(PrefetchController, name, controller_scope)

    def controller_query(method):
        def invoke(self, request_id):
            with service.gate:
                if service.controllers.get(request_id) is not service.current():
                    raise RuntimeError("Prefetch completion owner mismatch")
                result = method(self, request_id)
                if result is not None:
                    service.controllers.pop(request_id)
                return result
        return invoke
    patch(PrefetchController, "query_prefetch_result", controller_query)

    # L2 store consumers pin their own exact acquired reservations until the
    # adapter reports terminal. Partition tokens into the real task records.
    patch(StoreListener, "pop_pending_keys", lambda method:
          lambda self: service.pop_store_batch(self, method))
    patch(StoreController, "_process_new_keys", lambda method:
          lambda self, keys: service.process_store_batch(self, method, keys))

    def store_submit(method):
        def invoke(self, keys):
            with service.gate:
                owner = Owner("l2-store:" + secrets.token_hex(16))
                before = set(self._in_flight_tasks)
                try:
                    with service.bind(owner):
                        method(self, keys)
                finally:
                    tasks = [(k, self._in_flight_tasks[k]) for k in set(self._in_flight_tasks) - before]
                    service.partition_store_reads(owner, tasks)
        return invoke
    patch(StoreController, "_submit_store_for_single_shape", store_submit)

    def store_finalize(method):
        def invoke(self, task_key, task):
            with service.gate, service.bind(task._cachepilot_owner):
                owner = service.current()
                try:
                    return method(self, task_key, task)
                except Exception as exc:
                    owner.error = repr(exc)
                    service.record("ownership_unresolved", owner.request_id, error=owner.error)
                    raise
        return invoke
    patch(StoreController, "_finalize_store", store_finalize)

    def lookup_init(method):
        def initialize(self, *args, **kwargs):
            method(self, *args, **kwargs)
            service.lookup = self
            service.thread = threading.Thread(target=service.run, daemon=True, name="owned-service-reaper")
            service.thread.start()
        return initialize
    patch(LookupModule, "__init__", lookup_init)

    def lookup(method):
        def invoke(self, key, tp_size):
            with service.gate:
                if key.world_size != 1 or key.require_num_kv_readers() != 1 or key.start != 0:
                    raise ValueError("Owned service supports TP=1, one reader, zero-start lookup")
                attn = self._ctx.layout_desc_registry.find_attn_desc(key.model_name, key.world_size)
                if attn and (attn.num_object_groups != 1
                        or tuple(attn.group_kinds) != ("attention",)
                        or tuple(attn.num_chunks_in_sw) != (-1,)):
                    raise ValueError("Owned service requires one full-attention object group")
                if not service.admit(key):
                    service.record("lookup_rejected_late", key.request_id)
                    return None
                owner = service.jobs[key.request_id] = Owner(key.request_id, key=key)
                try:
                    with service.bind(owner):
                        result = method(self, key, tp_size)
                    if owner.handle is None:
                        owner.consumed = True
                    service.record("lookup_registered", key.request_id, objects=len(owner.reads))
                    return result
                except Exception as exc:
                    owner.error = repr(exc)
                    raise
        return invoke
    patch(LookupModule, "lookup", lookup)

    def query(method):
        def invoke(self, request_id):
            with service.gate:
                owner = service.jobs.get(request_id)
                if owner is None or owner.abandoned or owner.error or owner.consumed:
                    return 0
                # Native QUERY keeps all its metrics/session behavior. Its
                # destructive storage query runs under the original owner.
                with service.bind(owner):
                    job = self._prefetch_jobs.get(request_id)
                    if owner.handle is None and job is not None:
                        self._prefetch_jobs.pop(request_id)
                        return 0
                    result = method(self, request_id)
                if result is not None:
                    owner.consumed = True
                    owner.hit_chunks = result
                    job_keys = tuple(owner.keys)
                    from lmcache.lmcache_native import Bitmap
                    found = Bitmap(len(job_keys))
                    held = {r[0] for r in owner.reads}
                    found.batched_set([i for i, key in enumerate(job_keys) if key in held])
                    count, retained = fold_unfold_ranked(found, len(job_keys), 1, (-1,))
                    if count != result:
                        raise RuntimeError("Native QUERY differs from original token ledger")
                    service.release(owner, (found & ~retained).gather(job_keys))
                return result
        return invoke
    patch(LookupModule, "query_prefetch_status", query)

    def end(method):
        def invoke(self, request_id):
            with service.gate:
                service.end(request_id)
                result = method(self, request_id)
                service.sweep()
                return result
        return invoke
    patch(LookupModule, "end_session", end)

    def free(method):
        def invoke(self, key, tp_size):
            with service.gate:
                owner = service.jobs.get(key.request_id)
                if owner is None or owner.abandoned or owner.error:
                    return
                if not owner.consumed:
                    raise RuntimeError("FREE before completion consumption")
                groups = {k.object_group_id for k in owner.keys}
                keys = set(k for group in self._ctx.resolve_obj_keys(key, sorted(groups)) for k in group)
                service.release(owner, keys)
        return invoke
    patch(LookupModule, "free_lookup_locks", free)

    def ping(method):
        def invoke(self, instance_id):
            if instance_id is not None and instance_id < 0:
                return service.heartbeat(-instance_id)
            return method(self, instance_id)
        return invoke
    patch(ManagementModule, "ping", ping)

    def report(method):
        def invoke(self):
            with service.gate:
                return method(self) | service.status()
        return invoke
    patch(LookupModule, "report_status", report)

    @contextmanager
    def read_buffers(self, keys):
        owner = service.current()
        l1 = self._l1_manager
        with l1._lock:
            buffers = []
            for key in keys:
                entry = l1._objects.get(key)
                records = [r for r in owner.reads if r[0] == key]
                if len(records) != 1 or entry is None or entry.read_lock is not records[0][1] or entry.write_lock.is_locked():
                    raise RuntimeError("Original pinned buffer missing")
                buffers.append(entry.memory_obj)
        yield buffers
    StorageManager.read_prefetched_results = read_buffers

    native_submit = transfer_module.submit_callback_to_stream
    def callback_submit(stream, kind, payload):
        if getattr(service.local, "task", None) is not None and kind in ("finish_read_prefetched", "finish_write"):
            return  # Replaced by one original-identity marker below.
        return native_submit(stream, kind, payload)
    transfer_module.submit_callback_to_stream = callback_submit

    def transfer_init(method):
        def initialize(self, *args, **kwargs):
            method(self, *args, **kwargs)
            service.transfer = self
            self.register_host_func("cachepilot.owned.terminal.v1", service.terminal, TerminalV1)
        return initialize
    patch(LMCacheDrivenTransferModule, "__init__", transfer_init)

    native_release_entries = LMCacheDrivenTransferModule._release_entries
    def release_entries(self, entries):
        with service.gate:
            active = [e for e in entries
                      if any(t["entry"] is e for t in service.transfers.values())]
            for entry in active:
                entries.remove(entry)
                if not any(e is entry for e in service.deferred_entries):
                    service.deferred_entries.append(entry)
            return native_release_entries(self, entries)
    LMCacheDrivenTransferModule._release_entries = release_entries
    LMCacheDrivenTransferModule._cachepilot_release_entries = native_release_entries

    for kind in ("retrieve", "store"):
        def transfer_call(method, kind=kind):
            def invoke(self, key, instance_id, gpu_block_ids, event_ipc_handle, *args, **kwargs):
                with service.gate:
                    if service.closing:
                        return b"", False
                    entry = self.get_and_touch_context_entry(instance_id)
                    parent = service.jobs.get(key.request_id)
                    if kind == "retrieve":
                        if (parent is None or parent.abandoned or parent.error or parent.claimed
                                or not parent.consumed or key.worker_id != 0
                                or (key.model_name, key.world_size, key.cache_salt, key.token_ids) !=
                                   (parent.key.model_name, parent.key.world_size, parent.key.cache_salt, parent.key.token_ids)
                                or not 0 <= key.start < key.end <= parent.key.end):
                            return b"", False
                        parent.claimed = True
                        owner = Owner(key.request_id, key=key, reads=list(parent.reads))
                        parent.reads.clear()
                    else:
                        owner = Owner(key.request_id, key=key)
                    if entry is None:
                        service.release(owner)
                        return b"", False
                    context = entry.cache_context
                    groups = context.kv_layer_groups_manager.num_kernel_groups
                    chunks = (key.end - key.start) // self._ctx.chunk_size
                    required = [chunks * context.calculate_num_blocks(self._ctx.chunk_size, group) for group in range(groups)]
                    skip = kwargs.get("skip_first_n_tokens", args[0] if args else 0)
                    invalid = (type(key.start) is not int or type(key.end) is not int
                            or not 0 <= key.start < key.end <= len(key.token_ids)
                            or key.start % self._ctx.chunk_size or key.end % self._ctx.chunk_size
                            or len(gpu_block_ids) != groups
                            or context.kv_layer_groups_manager.num_object_groups != 1
                            or any(len(ids) != need or any(type(i) is not int or not 0 <= i < context.num_blocks for i in ids)
                                   or len(set(i for i in ids if i)) != len([i for i in ids if i])
                                   for ids, need in zip(gpu_block_ids, required))
                            or type(skip) is not int or not 0 <= skip < key.end - key.start
                            or any(skip % group.tokens_per_block for group in context.kv_layer_groups_manager.kernel_groups)
                            or (kind == "retrieve" and (key.end != parent.hit_chunks * self._ctx.chunk_size
                                or any(0 in ids for ids in gpu_block_ids))))
                    if invalid:
                        service.release(owner)
                        service.record("transfer_rejected_blocks", key.request_id, kind=kind)
                        return b"", False
                    service.sequence += 1
                    payload = TerminalV1(service.sequence, secrets.token_hex(16), key.request_id, kind)
                    task = dict(payload=payload, owner=owner, entry=entry, returned=False,
                                seen=False, succeeded=False)
                    service.transfers[payload.sequence] = task
                    service.record("transfer_submitted", key.request_id, sequence=payload.sequence,
                                   kind=kind, read_pins=len(owner.reads), blocks=gpu_block_ids)
                    service.local.task = task
                    try:
                        with service.bind(owner):
                            result = method(self, key, instance_id, gpu_block_ids, event_ipc_handle, *args, **kwargs)
                        task["succeeded"] = result[1]
                    except Exception as exc:
                        service.record("transfer_call_failed", key.request_id, error=repr(exc))
                        # A partial enqueue may have occurred. Record a device
                        # terminal after it, so vLLM cannot reuse targets early.
                        try:
                            with transfer_module.torch_dev.device(context.device), transfer_module.torch_dev.stream(context.stream):
                                event = entry.event_backend.create_event(context.device)
                                task["error_event"] = event
                                entry.event_backend.record_event(event, context.stream)
                                result = entry.event_backend.export_event(event, context.device), False
                        except Exception as terminal_exc:
                            owner.error = "Cannot establish GPU terminal: " + repr(terminal_exc)
                            service.record("ownership_unresolved", key.request_id, error=owner.error)
                            raise
                    finally:
                        service.local.task = None
                    try:
                        native_submit(context.cupy_stream, "cachepilot.owned.terminal.v1", payload)
                    except Exception as exc:
                        owner.error = "Missing native terminal: " + repr(exc)
                        service.record("ownership_unresolved", key.request_id, error=owner.error)
                    task["returned"] = True
                    if task["seen"] and not owner.error:
                        service.retire(task)
                    return result
            return invoke
        patch(LMCacheDrivenTransferModule, kind, transfer_call)

    # Context teardown and allocator close are forbidden until all actual
    # consumers finish. A permanently stuck controller stays live and pinned.
    def lookup_close(method):
        def invoke(self):
            with service.gate:
                service.closing = True
                for request_id in tuple(service.jobs):
                    service.end(request_id)
            deadline = time.monotonic() + service.shutdown_timeout
            while time.monotonic() < deadline:
                service.sweep()
                with service.gate:
                    controller = service.storage._store_controller
                    if (not service.jobs and not service.transfers and not service.controllers
                            and not service.unresolved and not service.deferred_entries and not service.store_batches
                            and not controller._in_flight_tasks and not controller._listener.pending_count()):
                        service.stopped.set()
                        service.record("owned_shutdown_drained")
                        return method(self)
                time.sleep(0.05)
            service.record("owned_shutdown_blocked", **service.status())
            raise RuntimeError("Owned shutdown unresolved; allocator and contexts retained")
        return invoke
    patch(LookupModule, "close", lookup_close)
    for cls in (StorageManager, L1Manager, PrefetchController, StoreController,
                LMCacheDrivenTransferModule):
        name = "stop" if cls in (PrefetchController, StoreController) else "close"
        def safe_close(method):
            def invoke(self, *args, **kwargs):
                with service.gate:
                    if (service.jobs or service.controllers or service.transfers or service.unresolved
                            or service.deferred_entries or service.store_batches
                            or service.storage._store_controller._in_flight_tasks
                            or service.storage._store_controller._listener.pending_count()):
                        raise RuntimeError("Cannot close allocator/controller with owned consumers")
                    if not service.stopped.is_set():
                        raise RuntimeError("Lookup drain must seal admission before allocator/controller close")
                # stop() joins background loops, which may need the gate.
                return method(self, *args, **kwargs)
            return invoke
        patch(cls, name, safe_close)
    return service


if __name__ == "__main__":
    from lmcache.cli.main import main
    service = install_service(os.environ["CACHEPILOT_OWNED_SERVICE_DIR"])
    if os.environ.get("CACHEPILOT_OWNED_FAULT_KIND"):
        from owned_service_fault import install_fault
        install_fault(service)
    sys.argv[0] = "lmcache"
    sys.exit(main())
