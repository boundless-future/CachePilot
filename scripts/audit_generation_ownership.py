"""Independent native token/terminal/BlockPool audit for generation diagnostics."""
from collections import Counter
from pathlib import Path
try:
    from .audit_generation_pilot import rows
except ImportError:
    from audit_generation_pilot import rows


def audit_records(service, snapshots):
    acquired, released, submitted, terminals, retired = {}, {}, {}, {}, {}
    jobs, reclaimed = Counter(), Counter()
    assert service and sum(r['event'] == 'owned_shutdown_drained' for r in service) == 1
    for row in service:
        event = row['event']
        assert event not in ('ownership_unresolved', 'terminal_rejected',
            'owned_shutdown_blocked', 'lookup_rejected_late', 'client_expired')
        if event in ('reservation_acquired', 'reservation_released'):
            key = (row['lock_id'], row['epoch'], row['serial'])
            target = acquired if event == 'reservation_acquired' else released
            assert key not in target, 'Duplicate original reservation'
            target[key] = row
        if event in ('transfer_submitted', 'terminal_seen', 'transfer_retired'):
            key = (row['request_id'], row['sequence'], row['kind'])
            target = dict(transfer_submitted=submitted, terminal_seen=terminals,
                          transfer_retired=retired)[event]
            assert key not in target, 'Duplicate original transfer event'
            target[key] = row
        if event == 'lookup_registered':
            jobs[row['request_id']] += 1
        if event == 'lookup_reclaimed':
            reclaimed[row['request_id']] += 1
    assert acquired and acquired.keys() == released.keys(), 'Original reservation missing'
    assert jobs == reclaimed and all(n == 1 for n in jobs.values()), 'Lookup not reclaimed once'
    assert submitted and submitted.keys() == terminals.keys() == retired.keys(), 'Native terminal missing'
    for key, row in acquired.items():
        release = released[key]
        assert row['request_id'] == release['request_id'], 'Reservation owner changed'
        assert row['monotonic_ns'] < release['monotonic_ns']
        assert release['disposition'] in ('RELEASED', 'STALE_EPOCH')
    for key, start in submitted.items():
        terminal, done = terminals[key], retired[key]
        assert start['monotonic_ns'] < terminal['monotonic_ns'] < done['monotonic_ns']
        if start['kind'] == 'retrieve':
            pins = [identity for identity, row in acquired.items()
                if row['request_id'] == start['request_id']
                and row['monotonic_ns'] < start['monotonic_ns'] < released[identity]['monotonic_ns']]
            assert len(pins) == start['read_pins'], 'Original retrieve pin count mismatch'
            assert all(released[p]['monotonic_ns'] > terminal['monotonic_ns'] for p in pins), 'Early release'
    assert len(snapshots) >= 2, 'No BlockPool lifecycle snapshots'
    first, final = snapshots[0], snapshots[-1]
    assert first['free_blocks'] == final['free_blocks'], 'BlockPool did not restore'
    assert not final['registered_ids'] and not final['tracked_refs'] and not final['deferred_frees']
    return dict(passed=True, reservations=len(acquired), lookups=sum(jobs.values()),
                transfers=len(submitted), final_free_blocks=final['free_blocks'])


def audit_cell(directory):
    directory = Path(directory)
    service = rows(directory / 'owned-events', 'owned-service-*.jsonl')
    snapshots = [r for r in rows(directory / 'owned-events', 'owned-connector-*.jsonl')
                 if r['event'] == 'scheduler_snapshot']
    return audit_records(service, snapshots)
