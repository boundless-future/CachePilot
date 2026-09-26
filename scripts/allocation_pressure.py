"""Count successful physical allocations, independent of request block tables."""
from dataclasses import replace


class AllocationPressure:
    def __init__(self):
        self.pending = 0
        self.current_step = 0
        self.last_step = 0
        self.total_allocated = 0
        self.total_consumed = 0

    def record(self, count):
        self.pending += count
        self.current_step += count
        self.total_allocated += count

    def begin_step(self):
        self.last_step = self.current_step
        self.current_step = 0

    def consume(self):
        count = self.pending
        self.pending = 0
        self.total_consumed += count
        return count


def install_allocation_pressure(pool, policy, observer=None):
    """Install once; preserve allocation result and all policy side effects.

    Zero-token steps keep their counts until the next upstream drain. No new
    drain is introduced. This is consumption since the last policy observation,
    not a prediction of the next allocation and not a change to EMA time units.
    """
    installed = getattr(policy, '_cachepilot_allocation_pressure', None)
    if installed is not None:
        if installed[0] is not pool:
            raise ValueError('Cannot bind allocation pressure to another pool')
        return installed[1]
    counter = AllocationPressure()
    allocate, drain = pool.get_new_blocks, policy.drain

    def allocate_blocks(*args, **kwargs):
        result = allocate(*args, **kwargs)
        counter.record(len(result))
        return result

    def corrected_drain(signals):
        actual = counter.consume()
        if observer is not None:
            observer(original_new_blocks=signals.new_blocks_allocated,
                     actual_step_blocks=counter.last_step,
                     consumed_blocks=actual,
                     total_allocated=counter.total_allocated,
                     total_consumed=counter.total_consumed)
        return drain(replace(signals, new_blocks_allocated=actual))

    pool.get_new_blocks = allocate_blocks
    policy.drain = corrected_drain
    policy._cachepilot_allocation_pressure = (pool, counter)
    return counter
