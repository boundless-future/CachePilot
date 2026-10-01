"""Original writer publication after the installed CUDA native marker.

Standalone L1 contract only. The caller owns the source CUDA allocation and
must hold it and join all consumer accesses onto the submitted stream.
"""
import threading
import time
import uuid

import msgspec
from lmcache.v1.multiprocess.native_completion import submit_callback_to_stream


class WriterCompletionV1(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    version: int
    manager: str
    sequence: int
    nonce: str


class NativeWriterRuntime:
    def __init__(self, manager, dispatcher):
        self.manager = manager
        self.kind = 'cachepilot.writer.v1.' + uuid.uuid4().hex
        self.pending = {}
        self.completed = []
        self.gate = threading.RLock()
        self.accepting = True
        dispatcher.register(self.kind, self._complete, WriterCompletionV1)

    def _complete(self, payload):
        with self.gate:
            entry = self.pending.get(payload.sequence)
            if entry is None or payload != entry['payload']:
                return False
            entry['seen'] = True
            if entry['returned']:
                self._finish(entry)
            return True

    def _finish(self, entry):
        if self.manager.finish_writer(entry['handle'], succeeded=entry['succeeded'], terminal=True):
            self.pending.pop(entry['handle'].sequence)
            self.completed.append(dict(sequence=entry['handle'].sequence,
                succeeded=entry['succeeded'], native_callback=True, error=entry['error']))
        else:
            entry['error'] = 'Writer terminal publication/cleanup unresolved'

    def submit(self, handle, stream, consumer, *, recorder=submit_callback_to_stream):
        with self.gate, self.manager._lock:
            state = self.manager.writers.get(handle)
            if not self.accepting or handle.sequence in self.pending:
                return False
            if state is None or state['terminal'] or state['error']:
                raise ValueError('Original active writer required')
            entry = dict(handle=handle, buffer=state['buffer'], succeeded=False,
                seen=False, returned=False, error=None,
                payload=WriterCompletionV1(1, handle.manager, handle.sequence, uuid.uuid4().hex))
            self.pending[handle.sequence] = entry
        try:
            consumer(entry['buffer'], stream)
            entry['succeeded'] = True
        except Exception as exc:
            entry['error'] = repr(exc)
        try:
            recorder(stream, self.kind, entry['payload'])
        except Exception as exc:
            # Missing marker is unresolved even if the CUDA event later signals.
            entry['error'] = '; '.join(filter(None, (entry['error'], repr(exc))))
            return False
        with self.gate:
            entry['returned'] = True
            if entry['seen']:
                self._finish(entry)
        return entry['succeeded']

    def close(self, timeout=1):
        with self.gate:
            self.accepting = False
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self.gate:
                if not self.pending:
                    break
            time.sleep(.005)
        with self.gate:
            return dict(drained=not self.pending, unresolved=list(self.pending))
