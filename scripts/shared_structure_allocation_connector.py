"""Observe gross physical allocation after unchanged protection admission."""
from scripts.shared_structure_window_connector import SharedStructureWindowConnector
from scripts.shared_prefix_window_probe import WindowProtection


class AllocationWindowProtection(WindowProtection):
    def bind(self, manager):
        super().bind(manager)
        if getattr(self.pool, '_cachepilot_structure_allocation_observer', False):
            raise RuntimeError('Pool allocation observer already installed')
        original = self.pool.get_new_blocks
        self._step = 0
        self._calls = []
        self._boundary = None

        def allocate(*args, **kwargs):
            if self._boundary is None:
                raise RuntimeError('Allocation outside observed scheduler boundary')
            before = self.pool.get_num_free_blocks()
            result = original(*args, **kwargs)
            self._calls.append(dict(count=len(result), free_before=before,
                free_after=self.pool.get_num_free_blocks(), block_ids=[b.block_id for b in result]))
            return result
        self.pool.get_new_blocks = allocate
        self.pool._cachepilot_structure_allocation_observer = True

    def prepare(self, scheduler):
        if self._boundary is not None:
            raise RuntimeError('Previous allocation observation not settled')
        from scripts.bounded_protection import conservative_reserve
        self._step += 1
        self._calls = []
        self._boundary = dict(step=self._step, free=self.pool.get_num_free_blocks(),
            usable_blocks=sum(not b.is_null for b in self.pool.blocks),
            reserve=conservative_reserve(scheduler), watermark=scheduler.kv_cache_manager.watermark_blocks,
            live_requests=len(scheduler.requests))
        return super().prepare(scheduler)

    def observe_completed_step(self, output):
        if self._boundary is None:
            raise RuntimeError('Missing allocation observation boundary')
        self.record('structure_actual_allocation', **self._boundary,
            calls=self._calls, allocated=sum(c['count'] for c in self._calls),
            scheduled_tokens=output.total_num_scheduled_tokens,
            free_after=self.pool.get_num_free_blocks())
        self._boundary = None


class SharedStructureAllocationConnector(SharedStructureWindowConnector):
    def _make_bounded_protection(self, config):
        return AllocationWindowProtection(self, config)

    def build_connector_meta(self, output):
        result = super().build_connector_meta(output)
        self._bounded.observe_completed_step(output)
        return result
