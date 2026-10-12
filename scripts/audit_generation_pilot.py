"""Fail-closed exact-generation audit; separate from replay/resource checks."""
from collections import Counter
import json
from pathlib import Path


def rows(directory, pattern):
    return [json.loads(line) for path in sorted(Path(directory).glob(pattern))
            for line in path.read_text().splitlines() if line.strip()]


def audit_rows(handoffs, service, result):
    scheduler = [r for r in handoffs if r.get('role') == 'SCHEDULER']
    registered = [r['wire_id'] for r in scheduler if r['event'] == 'generation_registered']
    submitted = [r['wire_id'] for r in scheduler if r['event'] == 'generation_end_submitted']
    returned = [r['request_id'] for r in service if r['event'] == 'generation_server_end_returned']
    before = [r for r in scheduler if r['event'] == 'generation_before_shutdown']
    wire_set = set(registered)
    observed = {r['request_id'] for r in service if r['event'] in (
        'lookup_registered', 'transfer_submitted')}
    store_only = {r['request_id'] for r in service if r['event'] == 'transfer_submitted'} - {
        r['request_id'] for r in service if r['event'] == 'lookup_registered'}
    active = [op for row in before for g in row['generations'] for op in g['operations']
              if op['state'] not in ('terminal', 'dropped')]
    batches = {phase: Counter(r['batch_id'] for r in handoffs if r['event'] == phase)
               for phase in ('scheduler_built', 'worker_bound', 'worker_submit_returned',
                             'worker_receipt', 'scheduler_receipt')}
    checks = dict(
        nonempty_unique_generations=bool(registered) and len(registered) == len(wire_set),
        exact_scheduler_ends=Counter(submitted) == Counter(registered),
        exact_server_ends=Counter(returned) == Counter(registered),
        observed_wires_registered=observed <= wire_set,
        server_end_removed=all(not r['session_remains'] for r in service
                              if r['event'] == 'generation_server_end_returned'),
        no_late_lookup=not any(r['event'] == 'lookup_rejected_late' for r in service),
        original_batch_handoff=bool(batches['scheduler_built']) and
            all(count == 1 for count in batches['scheduler_built'].values()) and
            all(value == batches['scheduler_built'] for value in batches.values()),
        scheduler_terminal=len(before) == 1 and not active and all(
            g['sealed'] and g['end_state'] == 'submitted' for row in before for g in row['generations'])
            and all(not row[k] for row in before for k in (
                'pending_store', 'inflight_store', 'retrieves', 'deferred_ends', 'pending_rpc')),
    )
    for key in ('status_before_engine_stop', 'status_after_engine_stop'):
        snapshot = result.get(key, {}).get('generation_observer')
        checks[key + '_exact_empty'] = bool(snapshot is not None) and all(
            snapshot.get(name) == [] for name in ('session_ids', 'owned_job_ids', 'transfer_wire_ids'))
    return dict(passed=all(checks.values()), checks=checks,
        registered=len(registered), scheduler_ends=len(submitted), server_ends=len(returned),
        store_only_wires=sorted(store_only), observed_wires=len(observed),
        batches=len(batches['scheduler_built']), active_operations=active,
        scope='Original server END handler returns plus exact empty snapshots; not a transport ACK claim')


def audit_cell(directory):
    directory = Path(directory)
    return audit_rows(rows(directory / 'handoffs', '*.jsonl'),
        rows(directory / 'owned-events', '*.jsonl'),
        json.loads((directory / 'result.json').read_text()))
