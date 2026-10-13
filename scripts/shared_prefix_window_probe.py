"""Read-only candidate-window diagnostics around the unchanged shared helper."""
from scripts.shared_prefix_connector import SharedPrefixServiceProtection
from scripts.shared_prefix_probe import SharedPrefixProbeConnector
from scripts.bounded_protection import conservative_reserve
from scripts.protection_handoff import describe


class WindowProtection(SharedPrefixServiceProtection):
    def prepare(self, scheduler):
        reserve = conservative_reserve(scheduler)
        free = self.pool.get_num_free_blocks()
        ranks = self.policy._free_queue_ranks(free)
        pending = []
        for rid, operations in self.policy._pending.items():
            candidate = operations[0]
            meta = candidate.store_metadata
            pending.append(dict(**describe(meta), live=rid in scheduler.requests,
                finished=self.manager._requests.is_finished(rid),
                inflight=self.manager._requests.has_in_flight(rid),
                protected=rid in self.inflight,
                blocks=[dict(id=bid, refs=self.pool.blocks[bid].ref_cnt,
                    rank=ranks.get(bid), null=self.pool.blocks[bid].is_null,
                    hash_matches=self.pool.blocks[bid].block_hash == candidate.block_hashes.get(bid),
                    tracked=bid in candidate.block_hashes)
                    for group in meta.op.block_ids for bid in group]))
        self.record('shared_window', free=free, reserve=reserve, budget=self.budget,
            used=sum(len(p.blocks) for p in self.inflight.values()), pending=pending)
        return super().prepare(scheduler)


class SharedPrefixWindowProbeConnector(SharedPrefixProbeConnector):
    def _make_bounded_protection(self, config):
        return WindowProtection(self, config)
