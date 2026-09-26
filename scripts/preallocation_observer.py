"""Read-only scheduler demand snapshots for diagnostic runs only."""


def _ceil_div(value, divisor):
    return (value + divisor - 1) // divisor


def demand_snapshot(scheduler):
    """Estimate compute-slot demand without invoking cache or remote lookup.

    Async KV loads do not consume compute slots, so this is not an upper bound
    on all physical allocations in the step.
    """
    pool = scheduler.kv_cache_manager.block_pool
    managers = scheduler.kv_cache_manager.coordinator.single_type_managers
    if len(managers) != 1 or managers[0].__class__.__name__ != 'FullAttentionManager':
        raise RuntimeError('Preallocation observer requires one full-attention KV group')
    block_size = scheduler.block_size
    budget = scheduler.max_num_scheduled_tokens
    slots = max(0, scheduler.max_num_running_reqs - len(scheduler.running)
                - scheduler.num_waiting_for_streaming_input)
    waiting = list(scheduler.waiting) + list(scheduler.skipped_waiting)
    requests = []
    running_demand = 0
    for request in scheduler.running:
        remaining = max(0, request.num_tokens_with_spec - request.num_computed_tokens)
        resident = len(managers[0].req_to_blocks.get(request.request_id, ()))
        tokens = min(remaining, budget)
        added = max(0, _ceil_div(request.num_computed_tokens + tokens, block_size) - resident)
        running_demand += added
        budget -= tokens
        requests.append(dict(request_id=request.request_id, queue='running',
                             status=request.status.name, remaining_tokens=remaining,
                             resident_blocks=resident, estimated_blocks=added))
    waiting_demand = 0
    for index, request in enumerate(waiting):
        remaining = max(0, request.num_tokens_with_spec - request.num_computed_tokens)
        resident = len(managers[0].req_to_blocks.get(request.request_id, ()))
        eligible = (index < slots and budget > 0 and
                    request.status.name in {'WAITING', 'PREEMPTED'})
        # External KV may be loaded asynchronously in full even when the
        # compute token budget is smaller. This is an upper bound, not a hit estimate.
        upper = max(0, _ceil_div(request.num_tokens_with_spec, block_size) - resident) if eligible else 0
        waiting_demand += upper
        if eligible:
            budget -= min(remaining, budget)
        requests.append(dict(request_id=request.request_id, queue='waiting',
                             status=request.status.name, remaining_tokens=remaining,
                             resident_blocks=resident, estimated_blocks_upper=upper,
                             eligible=eligible))
    return dict(upcoming_step=scheduler.current_step + 1,
                block_size=block_size, free_blocks=pool.get_num_free_blocks(),
                token_budget=scheduler.max_num_scheduled_tokens,
                free_slots=slots, running_demand_blocks=running_demand,
                waiting_demand_upper_blocks=waiting_demand,
                predicted_upper_blocks=running_demand + waiting_demand,
                requests=requests)


def install_scheduler_observer(scheduler_type):
    """Wrap the pinned scheduler once; only opt-in connectors receive snapshots."""
    if getattr(scheduler_type.schedule, '_cachepilot_preallocation_observer', False):
        return
    original = scheduler_type.schedule

    def observed_schedule(scheduler, *args, **kwargs):
        observer = getattr(scheduler.connector, 'observe_preallocation', None)
        if observer is not None:
            observer(demand_snapshot(scheduler))
        return original(scheduler, *args, **kwargs)

    observed_schedule._cachepilot_preallocation_observer = True
    scheduler_type.schedule = observed_schedule
