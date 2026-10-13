"""Read-only native capacity-call accounting; never compute a pin budget."""
import inspect


def install_native_allocation_observer(manager, record, context):
    if getattr(manager, '_cachepilot_native_allocation_observer', False):
        raise RuntimeError('Native allocation observer already installed')
    signature = inspect.signature(type(manager).allocate_slots)
    original_allocate = manager.allocate_slots
    original_demand = manager.coordinator.get_num_blocks_to_allocate
    pool = manager.block_pool
    original_get = pool.get_new_blocks
    original_touch = pool.touch
    active = None
    serial = 0

    def demand(*args, **kwargs):
        value = original_demand(*args, **kwargs)
        if active is not None:
            active['demands'].append(dict(required=value, free=pool.get_num_free_blocks()))
        return value

    def get(*args, **kwargs):
        result = original_get(*args, **kwargs)
        if active is not None:
            active['new_blocks'] += len(result)
        return result

    def touch(blocks):
        # Upstream accepts a sequence; do not consume an arbitrary iterator.
        consumed = sum(b.ref_cnt == 0 and not b.is_null for b in blocks)
        result = original_touch(blocks)
        if active is not None:
            active['touched_free_blocks'] += consumed
        return result

    def allocate(*args, **kwargs):
        nonlocal active, serial
        if active is not None:
            raise RuntimeError('Nested native allocation call')
        bound = signature.bind(manager, *args, **kwargs)
        bound.apply_defaults()
        values = bound.arguments
        request = values['request']
        serial += 1
        active = dict(serial=serial, **context(), request_id=request.request_id,
            status=request.status.name, free_before=pool.get_num_free_blocks(),
            num_new_tokens=values['num_new_tokens'],
            num_new_computed_tokens=values['num_new_computed_tokens'],
            num_external_computed_tokens=values['num_external_computed_tokens'],
            delay_cache_blocks=values['delay_cache_blocks'],
            full_sequence_must_fit=values['full_sequence_must_fit'],
            reserved_blocks=values['reserved_blocks'],
            watermark=manager.watermark_blocks if values['has_scheduled_reqs']
                and request.status.name in ('WAITING', 'PREEMPTED') else 0,
            demands=[], new_blocks=0, touched_free_blocks=0)
        try:
            result = original_allocate(*args, **kwargs)
        except BaseException as exc:
            active.update(success=False, error=repr(exc), free_after=pool.get_num_free_blocks())
            record('native_allocation_call', **active)
            raise
        else:
            active.update(success=result is not None, error=None, free_after=pool.get_num_free_blocks())
            record('native_allocation_call', **active)
            return result
        finally:
            active = None

    manager.coordinator.get_num_blocks_to_allocate = demand
    pool.get_new_blocks = get
    pool.touch = touch
    manager.allocate_slots = allocate
    manager._cachepilot_native_allocation_observer = True
