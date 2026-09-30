"""Small reviewable 3C fake-worker scenarios; not latency/performance results."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path

from protection_state_machine import Block, FakeBlockPool, ProtectionMachine


def scenario(name, budget):
    blocks = tuple(Block(i, 1, f"prefix-{i}") for i in range(12))
    pool = FakeBlockPool(blocks, 16)
    policy = ProtectionMachine(pool, budget, 2, reserve_blocks=2)
    session = policy.arrive("agent-session")
    demand = 15 if name == "insufficient_headroom" else 10
    tokens = 0 if name == "zero_token_step" else 1
    if name == "stale_hash":
        pool.overwrite(Block(2, 2, "reused-prefix"))
    decision = policy.prepare(session, blocks[:4], 0, demand, tokens)
    events = [dict(event="prepare", **asdict(decision), allocatable=pool.allocatable)]
    action = policy.submit(decision.token) if decision.token is not None else None
    if action:
        events.append(dict(event="store_submitted", token=action.token,
                           chunk_ids=[b.block_id for b in action.blocks]))
    allocations = pool.allocate(demand)
    policy.assert_invariants()
    events.append(dict(event="allocator_pressure", allocated_ids=list(allocations),
                       prefix_still_valid=[pool.matches(b) for b in blocks[:4]],
                       allocatable=pool.allocatable))
    if name == "cancel_inflight":
        policy.cancel(session)
        events.append(dict(event="cancel", outstanding_pins=sum(pool.pins.values())))
    stored = 0
    if action:
        assert all(pool.matches(b) for b in action.blocks)
        success = name != "store_failure"
        stored = (min(2, len(action.blocks)) if name == "partial_store" else len(action.blocks)) if success else 0
        policy.receipt(action.token, success, stored)
        events.append(dict(event="store_receipt", success=success, stored_prefix=stored))
    pool.release_allocations(allocations)
    policy.assert_invariants()
    saved = policy.saved.get(session)
    policy.reset()
    assert not policy.batches and not pool.pins
    return dict(scenario=name, max_pins=budget, model_stored_chunks=stored,
                current_generation_saved_prefix=saved, pins_acquired=len(pool.pin_events),
                pins_released=len(pool.unpin_events), resources_drained=True, events=events)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    names = ("safe_headroom", "insufficient_headroom", "zero_token_step", "stale_hash",
             "cancel_inflight", "store_failure", "partial_store")
    result = dict(kind="fake-worker correctness scenarios, not measured GPU performance",
                  demand_source="supplied oracle; no production predictor",
                  model_block_semantics="one complete KV chunk per physical block",
                  runs=[scenario(name, budget) for name in names for budget in (0, 4)])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(dict(scenarios=len(result["runs"]), all_drained=True)))


if __name__ == "__main__":
    main()
