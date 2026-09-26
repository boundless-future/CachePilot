"""Audit online pre-step warnings against subsequent physical allocations."""
import argparse
import json
from pathlib import Path
from math import ceil


def _async_admission_upper(snapshot):
    block_size = snapshot['block_size']
    waiting = [request for request in snapshot['requests']
               if request['queue'] == 'waiting' and
               request['status'] in {'WAITING', 'PREEMPTED'}]
    potential = sum(max(0, (request['remaining_tokens'] + block_size - 1) // block_size
                        - request['resident_blocks']) for request in waiting[:4])
    return max(snapshot['predicted_upper_blocks'],
               snapshot['running_demand_blocks'] + potential)


def _lookup_upper(snapshot, forecast):
    block_size = snapshot['block_size']
    waiting = [request for request in snapshot['requests']
               if request['queue'] == 'waiting' and request['status'] == 'WAITING'][:4]
    potential = 0
    for request in waiting:
        if 'lookup_state' not in request:
            raise ValueError('Lookup forecast requires lookup-state snapshots')
        state = request['lookup_state']
        hit = request['remote_hit_tokens']
        if state in {'resolved', 'result_available'}:
            tokens = min(hit, request['remaining_tokens'])
        elif forecast == 'lookup_inflight' and state in {'awaiting_ack', 'status_pending'}:
            tokens = request['remaining_tokens']
        else:
            tokens = 0
        potential += max(0, ceil(tokens / block_size) - request['resident_blocks'])
    return max(snapshot['predicted_upper_blocks'],
               snapshot['running_demand_blocks'] + potential)


def _forecast_blocks(snapshot, forecast):
    if forecast == 'compute_slots':
        return snapshot['predicted_upper_blocks']
    if forecast == 'async_admission':
        return _async_admission_upper(snapshot)
    if forecast in {'lookup_ready', 'lookup_inflight'}:
        return _lookup_upper(snapshot, forecast)
    raise ValueError(f'Unknown forecast: {forecast}')


def _store_timing(rows):
    submissions = {}
    durations = []
    unmatched = 0
    submitted = 0
    for row in sorted(rows, key=lambda item: item['monotonic_ns']):
        if row['event'] == 'store_submit':
            for request_id in row['request_ids']:
                submissions.setdefault(request_id, []).append(row['monotonic_ns'])
                submitted += 1
        elif row['event'] == 'store_worker_receipt':
            for request_id in row['completed']:
                queue = submissions.get(request_id, [])
                if not queue:
                    unmatched += 1
                    continue
                durations.append((row['monotonic_ns'] - queue.pop(0)) / 1e6)
    if not submitted and not unmatched:
        return None
    ordered = sorted(durations)
    return dict(submitted_batches=submitted, completed_batches=len(ordered),
                unmatched_receipts=unmatched,
                unfinished_batches=sum(map(len, submissions.values())),
                worker_receipt_latency_ms=None if not ordered else dict(
                    p50=ordered[(len(ordered)-1)//2],
                    p95=ordered[ceil(.95*len(ordered))-1], maximum=ordered[-1]))


def analyze(rows, burst_blocks=128, forecast='compute_slots'):
    snapshots = {row['upcoming_step']: row for row in rows if row['event'] == 'pre_step'}
    allocations = {}
    for row in rows:
        if row['event'] == 'allocation':
            allocations.setdefault(row['upcoming_step'], []).append(row)
    if not snapshots or not allocations:
        raise ValueError('Expected pre-step snapshots and allocation events')

    actual = {step: sum(item['allocated_blocks'] for item in allocations.get(step, ()))
              for step in snapshots}
    warnings = []
    for step, snapshot in sorted(snapshots.items()):
        predicted = _forecast_blocks(snapshot, forecast)
        warnings.append(dict(step=step, predicted_upper_blocks=predicted,
                             actual_blocks=actual[step],
                             warning=predicted >= burst_blocks,
                             historical_warning=snapshot.get('historical_ema_blocks', 0) >= burst_blocks,
                             burst=actual[step] >= burst_blocks))

    bursts = []
    for entry in warnings:
        if not entry['burst']:
            continue
        step = entry['step']
        earlier = [row for row in warnings if row['step'] < step and row['warning']]
        prior = earlier[-1] if earlier else None
        earlier_historical = [row for row in warnings if row['step'] < step and row['historical_warning']]
        prior_historical = earlier_historical[-1] if earlier_historical else None
        lead_ms = ((snapshots[step]['monotonic_ns'] -
                    snapshots[prior['step']]['monotonic_ns']) / 1e6) if prior else None
        bursts.append(dict(step=step, actual_blocks=entry['actual_blocks'],
                           warned_same_step=entry['warning'], prior_warning_step=None if prior is None else prior['step'],
                           prior_warning_lead_ms=lead_ms,
                           historical_warning_same_step=entry['historical_warning'],
                           prior_historical_step=None if prior_historical is None else prior_historical['step']))

    # This selection uses only the snapshot available before each schedule call.
    # A positive is an allocator-observed overwrite of that same operation.
    risk = {}
    for step, snapshot in snapshots.items():
        for pending in snapshot['pending']:
            rank = pending['nearest_free_rank']
            predicted = _forecast_blocks(snapshot, forecast)
            if (rank is not None and pending['hash_valid'] and not pending['blocked'] and
                    rank < predicted):
                key = (pending['request_id'], pending['start'], pending['end'])
                risk.setdefault(key, []).append(snapshot)
    losses = {}
    for step, events in allocations.items():
        for event in events:
            for affected in event.get('affected', ()):
                if not affected.get('intact_before_ids'):
                    continue
                key = (affected['request_id'], affected['start'], affected['end'])
                losses.setdefault(key, []).append(event)
    cases = []
    for key, events in losses.items():
        first = min(events, key=lambda row: row['monotonic_ns'])
        prior = [row for row in risk.get(key, ()) if row['monotonic_ns'] < first['monotonic_ns']]
        earliest = min(prior, key=lambda row: row['monotonic_ns']) if prior else None
        cases.append(dict(request_id=key[0], start=key[1], end=key[2],
                          loss_step=first['upcoming_step'],
                          online_warning_step=None if earliest is None else earliest['upcoming_step'],
                          warning_before_loss_step=(earliest is not None and
                                                    earliest['upcoming_step'] < first['upcoming_step']),
                          lead_ms=None if earliest is None else
                          (first['monotonic_ns']-earliest['monotonic_ns'])/1e6))

    warned_steps = sum(item['warning'] for item in warnings)
    true_steps = sum(item['warning'] and item['burst'] for item in warnings)
    result = dict(note='Diagnostic timing only. Warnings are not STORE completion or proven latency gains.',
                forecast=forecast,
                burst_threshold_blocks=burst_blocks, steps=len(warnings),
                physical_blocks=sum(actual.values()), burst_steps=len(bursts),
                warned_steps=warned_steps, same_step_true_warnings=true_steps,
                same_step_false_warnings=warned_steps-true_steps,
                historical_warned_steps=sum(item['historical_warning'] for item in warnings),
                bursts=bursts, affected_operations=len(cases),
                affected_with_prior_online_risk=sum(case['warning_before_loss_step'] for case in cases),
                affected_with_same_step_only_risk=sum(case['online_warning_step'] == case['loss_step']
                                                      for case in cases),
                affected_with_at_least_50ms_lead=sum(case['lead_ms'] is not None and
                                                      case['lead_ms'] >= 50 for case in cases),
                risk_warned_operations=len(risk),
                risk_warned_without_observed_reuse=len(set(risk) - set(losses)),
                affected_cases=cases)
    store_timing = _store_timing(rows)
    if store_timing is not None:
        result['store_timing'] = store_timing
    if forecast == 'compute_slots':
        alternate = analyze(rows, burst_blocks, 'async_admission')
        result['posthoc_async_admission_upper'] = {
            key: value for key, value in alternate.items()
            if key in {'warned_steps', 'same_step_true_warnings',
                       'same_step_false_warnings', 'affected_with_prior_online_risk',
                       'affected_with_same_step_only_risk', 'affected_with_at_least_50ms_lead',
                       'risk_warned_operations', 'risk_warned_without_observed_reuse',
                       'affected_cases', 'bursts'}}
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('ledger', nargs='+', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--burst-blocks', type=int, default=128)
    parser.add_argument('--forecast', choices=['compute_slots', 'async_admission',
                                               'lookup_ready', 'lookup_inflight'],
                        default='compute_slots')
    args = parser.parse_args()
    rows = [json.loads(line) for path in args.ledger for line in path.read_text().splitlines()]
    result = analyze(rows, args.burst_blocks, args.forecast)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({key: value for key, value in result.items()
                      if key not in {'bursts', 'affected_cases'}}, indent=2))


if __name__ == '__main__':
    main()
