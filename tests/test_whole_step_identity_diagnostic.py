import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from analyze_whole_step_identity import analyze_rows, prefix_sha, reuse_gaps


def evidence(route="recompute_prefix"):
    prompt = list(range(64))
    trace = {"events": [
        dict(event_id=0, arrival_ms=0, prompt=prompt),
        dict(event_id=1, arrival_ms=100, prompt=prompt + [64]),
    ]}
    identities = {i: f"cachepilot-trace-{i:04d}" for i in range(2)}
    results = [dict(event_id=i, api_request_id=api_id,
                    response_id=f"cmpl-{api_id}", error=None)
               for i, api_id in identities.items()]
    request_data = dict(results=results)
    raw = {i: f"cmpl-{api_id}-0" for i, api_id in identities.items()}
    prefix = prefix_sha(prompt[:32])
    wire = {i: f"wire-{i}" for i in range(2)}
    handoffs = [
        dict(event="generation_registered", role="SCHEDULER", request_id=raw[i],
             wire_id=wire[i], monotonic_ns=10 + i * 10) for i in range(2)
    ]
    handoffs.extend([
        dict(event="worker_bound", role="WORKER", source="protection",
             request_id=raw[0], wire_id=wire[0], batch_id="protected-0",
             prefix_sha256=prefix, end=32, start=0, direction="STORE",
             block_ids=[[1, 2]], monotonic_ns=12),
        dict(event="scheduler_receipt", role="SCHEDULER", source="protection",
             direction="STORE", request_id=raw[0], wire_id=wire[0],
             batch_id="protected-0", prefix_sha256=prefix, end=32,
             failed=False, monotonic_ns=25),
        dict(event="request_cache_match", role="SCHEDULER", request_id=raw[1],
             wire_id=wire[1], prompt_tokens=65, gpu_computed_tokens=0,
             external_tokens=32 if route == "lmcache_cpu_retrieve" else 0,
             monotonic_ns=35),
    ])
    service = [dict(event="transfer_submitted", request_id=wire[0], sequence=1,
                    kind="store", blocks=[[1, 2]], monotonic_ns=13),
               dict(event="terminal_seen", request_id=wire[0], sequence=1,
                    kind="store", monotonic_ns=23),
               dict(event="transfer_retired", request_id=wire[0], sequence=1,
                    kind="store", succeeded=True, monotonic_ns=24)]
    if route == "lmcache_cpu_retrieve":
        handoffs.append(dict(event="scheduler_metadata", role="SCHEDULER",
            request_id=raw[1], wire_id=wire[1], direction="RETRIEVE",
            prefix_sha256=prefix, end=32, start=0, skip=0,
            block_ids=[[3, 4]], monotonic_ns=36))
        service.extend([
            dict(event="transfer_submitted", request_id=wire[1], sequence=2, kind="retrieve",
                 blocks=[[3, 4]], monotonic_ns=37),
            dict(event="terminal_seen", request_id=wire[1], sequence=2, kind="retrieve", monotonic_ns=38),
            dict(event="transfer_retired", request_id=wire[1], sequence=2,
                 kind="retrieve", succeeded=True, monotonic_ns=39),
        ])
    return trace, request_data, handoffs, service


def test_stable_ids_join_to_scheduler_and_unmatched_prefix_is_recompute():
    result = analyze_rows(*evidence(), "two")
    assert result["passed"]
    assert result["mapped_requests"] == 2
    assert result["request_identity"][1]["raw_request_id"] == "cmpl-cachepilot-trace-0001-0"
    assert result["routes"][0]["route"] == "recompute_prefix"


def test_cpu_retrieve_requires_scheduler_op_and_successful_native_terminal():
    result = analyze_rows(*evidence("lmcache_cpu_retrieve"), "two")
    assert result["passed"]
    assert result["routes"][0]["route"] == "lmcache_cpu_retrieve"
    assert result["routes"][0]["generations"][0]["completed_retrieve_ops"] == 1
    trace, requests, handoffs, service = evidence("lmcache_cpu_retrieve")
    service = [r for r in service if r["event"] != "terminal_seen" or r["kind"] != "retrieve"]
    assert analyze_rows(trace, requests, handoffs, service, "two")["routes"][0]["route"] == "retrieve_unconfirmed"


def test_api_response_without_scheduler_generation_fails_closed():
    trace, requests, handoffs, service = evidence()
    handoffs = [row for row in handoffs if row.get("event") != "generation_registered"
                or row.get("request_id") != "cmpl-cachepilot-trace-0001-0"]
    result = analyze_rows(trace, requests, handoffs, service, "two")
    assert not result["passed"]
    assert any("No scheduler generation" in message for message in result["failures"])


def test_zero_uses_same_logical_target_without_foreign_batch_or_clock():
    data = evidence()
    target = analyze_rows(*data, "two")["protected_store_batches"]
    trace, requests, handoffs, service = data
    handoffs = [r for r in handoffs if r.get("source") != "protection"]
    result = analyze_rows(trace, requests, handoffs, [], "zero", target)
    assert result["passed"] and not result["protected_store_batches"]
    assert len(result["routes"]) == 1
    assert result["routes"][0]["protected_store_completed_before_cache_match"] is None
    assert result["routes"][0]["protected_store_completed_ns"] is None


def test_longer_retrieve_covers_target_but_wrong_tokens_or_blocks_do_not():
    trace, requests, handoffs, service = evidence("lmcache_cpu_retrieve")
    op = next(r for r in handoffs if r["event"] == "scheduler_metadata")
    op.update(end=64, prefix_sha256=prefix_sha(trace["events"][1]["prompt"][:64]))
    assert analyze_rows(trace, requests, handoffs, service, "two")["routes"][0]["route"] == "lmcache_cpu_retrieve"
    op["block_ids"] = [[999]]
    assert analyze_rows(trace, requests, handoffs, service, "two")["routes"][0]["route"] == "retrieve_unconfirmed"
    op["prefix_sha256"] = "wrong-prefix"
    assert analyze_rows(trace, requests, handoffs, service, "two")["routes"][0]["route"] == "external_hit_unconfirmed"


def test_async_none_poll_is_unknown_until_lookup_resolves():
    trace, requests, handoffs, service = evidence()
    lookup = next(r for r in handoffs if r["event"] == "request_cache_match")
    lookup["external_tokens"] = None
    assert analyze_rows(trace, requests, handoffs, service, "two")["routes"][0]["route"] == "lookup_unresolved"
    handoffs.append(dict(lookup, external_tokens=32, monotonic_ns=40))
    result = analyze_rows(trace, requests, handoffs, service, "two")
    assert result["routes"][0]["route"] == "external_hit_unconfirmed"
    assert result["routes"][0]["generations"][0]["cache_match_ns"] == 40


def test_reentry_keeps_each_generations_route_and_protection_timing():
    trace, requests, handoffs, service = evidence()
    raw = "cmpl-cachepilot-trace-0001-0"
    handoffs.extend([
        dict(event="generation_registered", role="SCHEDULER", request_id=raw,
             wire_id="reentry", monotonic_ns=50),
        dict(event="request_cache_match", role="SCHEDULER", request_id=raw,
             wire_id="reentry", gpu_computed_tokens=32, external_tokens=0, monotonic_ns=60),
    ])
    result = analyze_rows(trace, requests, handoffs, service, "two")
    route = result["routes"][0]
    assert route["route"] == "mixed_generation_routes"
    assert [g["route"] for g in route["generations"]] == ["recompute_prefix", "gpu_prefix_hit"]
    assert route["protected_store_completed_before_cache_match"] is True
    next(r for r in handoffs if r["event"] == "scheduler_receipt")["monotonic_ns"] = 45
    assert analyze_rows(trace, requests, handoffs, service, "two")["routes"][0]["protected_store_completed_before_cache_match"] is False


def test_reuse_gap_uses_first_resolved_observation_and_successful_source_store():
    source = dict(event_id=1, arrival_ms=0, prompt=list(range(768)))
    target_prompt = list(range(512)) + list(range(1000, 1256))
    target = dict(event_id=2, arrival_ms=10, prompt=target_prompt)
    raw_source, raw_target = "cmpl-cachepilot-trace-0001-0", "cmpl-cachepilot-trace-0002-0"
    wire_source, wire_target = "wire-source", "wire-target"
    handoffs = [
        dict(event="generation_registered", role="SCHEDULER", request_id=raw_target,
             wire_id=wire_target, monotonic_ns=18),
        dict(event="request_cache_match", role="SCHEDULER", request_id=raw_target,
             wire_id=wire_target, gpu_computed_tokens=0, external_tokens=None, monotonic_ns=20),
        dict(event="request_cache_match", role="SCHEDULER", request_id=raw_target,
             wire_id=wire_target, gpu_computed_tokens=256, external_tokens=0, monotonic_ns=30),
        dict(event="worker_bound", role="WORKER", request_id=raw_source, wire_id=wire_source,
             direction="STORE", start=0, end=256, prefix_sha256=prefix_sha(source["prompt"][:256]),
             block_ids=[[3]], batch_id="store-ok", monotonic_ns=10),
        dict(event="scheduler_receipt", role="SCHEDULER", request_id=raw_source, wire_id=wire_source,
             direction="STORE", batch_id="store-ok", failed=False, monotonic_ns=15),
        dict(event="worker_bound", role="WORKER", request_id=raw_source, wire_id=wire_source,
             direction="STORE", start=0, end=512, prefix_sha256=prefix_sha(source["prompt"][:512]),
             block_ids=[[4, 5]], batch_id="store-wrong-id", monotonic_ns=11),
        dict(event="scheduler_receipt", role="SCHEDULER", request_id=raw_source, wire_id="other-wire",
             direction="STORE", batch_id="store-wrong-id", failed=False, monotonic_ns=16),
    ]
    service = [
        dict(event="transfer_submitted", request_id=wire_source, sequence=1, kind="store",
             blocks=[[3]], monotonic_ns=12),
        dict(event="terminal_seen", request_id=wire_source, sequence=1, kind="store", monotonic_ns=13),
        dict(event="transfer_retired", request_id=wire_source, sequence=1, kind="store",
             succeeded=True, monotonic_ns=14),
        dict(event="transfer_submitted", request_id=wire_source, sequence=2, kind="store",
             blocks=[[4, 5]], monotonic_ns=13),
        dict(event="terminal_seen", request_id=wire_source, sequence=2, kind="store", monotonic_ns=14),
        dict(event="transfer_retired", request_id=wire_source, sequence=2, kind="store",
             succeeded=True, monotonic_ns=15),
    ]

    gaps = reuse_gaps(dict(events=[source, target]), handoffs, service)

    assert len(gaps) == 1
    assert gaps[0]["reusable_full_chunk_tokens"] == 512
    assert gaps[0]["first_gpu_tokens"] == 256
    assert gaps[0]["first_cache_match_uncovered_tokens"] == 256
    assert [r["end"] for r in gaps[0]["source_successful_store_ranges_before_cache_match"]] == [256]
    assert "not lookup issue time" in gaps[0]["scope"]
