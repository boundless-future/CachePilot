"""Natural-pressure lifecycle plus synchronizing KV integrity diagnostics."""
from scripts.diagnostic_connector import DiagnosticConnector
from scripts.late_store_receipt_connector import LateStoreReceiptConnector
from scripts.preemption_probe_range import whole_loaded_chunks


class PreemptionKVProbeConnector(DiagnosticConnector, LateStoreReceiptConnector):
    def _snapshot(self, *args):
        # Both diagnostics use this name; scheduler and KV observations have
        # distinct signatures. Keep each original observer unchanged.
        if len(args) == 1:
            return LateStoreReceiptConnector._snapshot(self, *args)
        phase, request_id, op, salt = args
        if phase == "after_retrieve":
            adapter = self.worker_adapter
            groups = adapter.engine_group_infos
            if len(groups) != 1 or groups[0].recurrent_state:
                raise RuntimeError("Only one dense group supported")
            original = op
            op = whole_loaded_chunks(op, block_tokens=groups[0].tokens_per_block,
                                     chunk_tokens=adapter.lmcache_tokens_per_chunk)
            if original.skip_first_n_tokens:
                self._observe("probe_skipped_partial_chunk", request_id=request_id,
                    start=original.start, end=original.end,
                    skip_tokens=original.skip_first_n_tokens,
                    compared_start=op.start if op else None)
            if op is None:
                return
        return DiagnosticConnector._snapshot(self, phase, request_id, op, salt)
