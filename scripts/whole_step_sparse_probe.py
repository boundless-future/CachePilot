"""Synchronizing correctness probe on the explicit whole-step service adapter."""
from dataclasses import replace

from cachepilot.integration.vllm.whole_step import WholeStepConnector
from scripts.protection_probe_connector import ProbeMixin
from scripts.diagnostic_connector import DiagnosticConnector, token_key
from scripts.preemption_probe_range import whole_loaded_chunks


class WholeStepSparseProbeConnector(ProbeMixin, WholeStepConnector):
    def _snapshot(self, *args):
        if len(args) == 2:
            return super()._snapshot(*args)
        phase, rid, op, salt = args
        adapter = self.worker_adapter
        chunk = adapter.lmcache_tokens_per_chunk
        span = adapter.engine_group_infos[0].tokens_per_block
        if phase == 'after_retrieve':
            op = whole_loaded_chunks(op, block_tokens=span, chunk_tokens=chunk)
            if op is None:
                return
        blocks = adapter._block_ids_per_group(op)
        if len(blocks) != 1 or op.start % chunk or op.end % chunk or op.skip_first_n_tokens:
            raise RuntimeError('Sparse probe requires aligned full chunks')
        protected = any(b['request_id'] == rid and b['source'] == 'protection'
            and b['start'] <= op.start and op.end <= b['end'] for b in self._bound_handoffs)
        for offset in range(0, op.end - op.start, chunk):
            begin, end = op.start + offset, op.start + offset + chunk
            key = token_key(op.token_ids, salt, end)
            ids = blocks[0][offset // span:(offset + chunk) // span]
            capture = protected if phase == 'before_store' else any(k[0] == key for k in self._probe_refs)
            if capture:
                partial = replace(op, start=begin, end=end, block_ids=[ids])
                DiagnosticConnector._snapshot(self, phase, rid, partial, salt)
            elif phase == 'before_store':
                self._record(dict(phase=phase, request_id=rid, salt=salt, start=begin,
                    end=end, token_prefix_sha256=key, block_ids=ids, layers=[], sparse_marker=True))
