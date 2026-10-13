"""Owner-thread RPC polling while the fixed synchronous core waits for input.

No model execution, scheduler ticks, or GPU block operations are synthesized.
An arrival wins immediately. RPC errors remain unresolved; no retries or timeout
based resource releases are performed. Only the explicit ledger connector opts in.
"""
import queue
from cachepilot.runtime.idle import IdleInputQueue


class GenerationInputQueue(IdleInputQueue):
    def get(self, block=True, timeout=None):
        connector = self.core.scheduler.connector
        if not block or timeout is not None or not connector.generation_needs_poll():
            return super().get(block=block, timeout=timeout)
        try:
            return self.original.get(block=True, timeout=0.1)
        except queue.Empty:
            if self.core.is_running() and not self.core.has_work() and self.original.empty():
                connector._flush_generation_ends()
            raise


def install_generation_idle_pump(core_type):
    if getattr(core_type._process_input_queue, '_cachepilot_generation_pump', False):
        return
    original = core_type._process_input_queue

    def process(core):
        connector = core.scheduler.connector
        if (hasattr(connector, 'generation_needs_poll')
                and not isinstance(core.input_queue, GenerationInputQueue)):
            # Preserve any already-installed idle wrapper without nesting it:
            # this subclass satisfies the inherited isinstance check.
            original_queue = core.input_queue
            if isinstance(original_queue, IdleInputQueue):
                original_queue = original_queue.original
            core.input_queue = GenerationInputQueue(original_queue, core)
        return original(core)
    process._cachepilot_generation_pump = True
    core_type._process_input_queue = process
