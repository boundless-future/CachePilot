"""Recompute idle-finalization diagnostic gates from requests and event logs.

This is not a KV content audit or a performance summarizer. Failed coverage
stays failed even when resources close; repeat replay is audited separately.
"""
import argparse
import hashlib
import json
from pathlib import Path


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def records(paths):
    return [json.loads(line) for path in sorted(paths)
            for line in path.read_text(encoding='utf-8').splitlines()]


def check_requests(data, trace):
    events = {e['event_id']: e for e in trace['events']}
    rows = {r['event_id']: r for r in data['results']}
    assert len(rows) == len(data['results']) == len(events), 'Missing/duplicate request'
    assert rows.keys() == events.keys(), 'Request identity mismatch'
    for key, row in rows.items():
        event = events[key]
        assert row['error'] is None and row['http_status'] == 200, 'Request error'
        assert row['prompt_sha256'] == event['prompt_sha256'], 'Prompt mismatch'
        assert row['usage']['prompt_tokens'] == event['prompt_tokens'], 'Input token mismatch'
        assert row['usage']['completion_tokens'] == event['max_tokens'], 'Output token mismatch'
        assert row['output_sha256'] == hashlib.sha256(row['output_text'].encode()).hexdigest()
    return rows


def check_resources(state, stopped=False):
    assert state['is_healthy']
    for key in ('active_sessions', 'owned_jobs', 'owned_controller_jobs',
                'owned_transfers', 'owned_deferred_contexts', 'owned_failed',
                'owned_read_pins', 'owned_store_batches', 'active_prefetch_jobs'):
        assert state[key] == 0, ('Resource residue', key)
    storage = state['storage_manager']
    for section, keys in (
            ('l1_manager', ('read_locked_count', 'write_locked_count', 'temporary_count')),
            ('store_controller', ('pending_keys_count', 'in_flight_task_count')),
            ('prefetch_controller', ('submission_queue_size', 'pending_queue_size',
                                    'in_flight_request_count', 'lookup_phase_count',
                                    'load_phase_count', 'completed_results_count'))):
        for key in keys:
            assert storage[section][key] == 0, ('Resource residue', section, key)
    if stopped:
        assert state['registered_gpu_ids'] == [], 'GPU registration residue'


def check_events(rows, drops):
    acquired, released = {}, {}
    submitted, terminal, retired = {}, {}, {}
    registered, reclaimed, ended = {}, {}, {}
    for row in rows:
        event = row['event']
        assert event != 'ownership_unresolved', 'Unresolved ownership'
        if event in ('reservation_acquired', 'reservation_released'):
            key = (row['lock_id'], row['epoch'], row['serial'])
            dest = acquired if event == 'reservation_acquired' else released
        elif event in ('transfer_submitted', 'terminal_seen', 'transfer_retired'):
            key = (row['request_id'], row['sequence'], row['kind'])
            dest = {'transfer_submitted': submitted, 'terminal_seen': terminal,
                    'transfer_retired': retired}[event]
        elif event in ('lookup_registered', 'lookup_reclaimed', 'diagnostic_end_session_enter'):
            key = row['request_id']
            dest = {'lookup_registered': registered, 'lookup_reclaimed': reclaimed,
                    'diagnostic_end_session_enter': ended}[event]
        else:
            continue
        assert key not in dest, ('Duplicate event', event, key)
        dest[key] = row
    assert registered and registered.keys() == reclaimed.keys() == ended.keys(), 'Job/END mismatch'
    assert acquired.keys() == released.keys(), 'Unreleased reservation'
    for key, start in acquired.items():
        end = released[key]
        assert end['monotonic_ns'] > start['monotonic_ns']
        assert end['request_id'] == start['request_id']
        assert end['disposition'] in ('RELEASED', 'STALE_EPOCH')
    assert submitted.keys() == terminal.keys() == retired.keys(), 'Missing native terminal'
    for key, start in submitted.items():
        end, done = terminal[key], retired[key]
        assert start['monotonic_ns'] < end['monotonic_ns'] < done['monotonic_ns']
        assert done['succeeded'], 'Unexpected failed transfer'
        if start['kind'] == 'retrieve':
            tokens = [token for token, r in acquired.items()
                      if r['request_id'] == start['request_id']
                      and r['monotonic_ns'] < start['monotonic_ns']
                      and released[token]['monotonic_ns'] > start['monotonic_ns']]
            assert len(tokens) == start['read_pins'], 'Read pin mismatch'
            assert all(released[t]['monotonic_ns'] > end['monotonic_ns'] for t in tokens)
    for key, start in registered.items():
        assert start['monotonic_ns'] < ended[key]['monotonic_ns'] <= reclaimed[key]['monotonic_ns']
    for drop in drops:
        key = drop['wire_id']
        assert key in ended and key in reclaimed, 'Dropped owner not ended/reclaimed'
        assert drop['acquired_pins'] == drop['submitted_stores'] == 0
        assert drop['dropped'] == len(drop['ranges']) > 0
    assert sum(r['event'] == 'owned_shutdown_drained' for r in rows) == 1
    return dict(jobs=len(registered), reservations=len(acquired),
                transfers=len(submitted), idle_drops=len(drops),
                native_terminal_before_read_release=True)


def check_raw_id_reuse(first, repeat, rows, drops):
    assert first.keys() == repeat.keys()
    lookups = [r for r in rows if r['event'] == 'lookup_registered']
    reclaimed = {r['request_id']: r for r in rows if r['event'] == 'lookup_reclaimed'}
    reentered_drops = []
    identities = []
    for key, original in first.items():
        successor = repeat[key]
        api_id = original['api_request_id']
        assert successor['api_request_id'] == api_id
        assert original['response_id'] == successor['response_id'] == 'cmpl-' + api_id
        raw_id = 'cmpl-' + api_id + '-0'
        pair = sorted((r for r in lookups if r['request_id'].split(':', 4)[-1] == raw_id),
                      key=lambda r: r['monotonic_ns'])
        assert len(pair) == 2, ('Expected two actual raw-ID generations', raw_id)
        old, new = pair
        assert old['request_id'] != new['request_id'], 'Wire generation reused'
        assert reclaimed[old['request_id']]['monotonic_ns'] < new['monotonic_ns'], 'Old owner not closed before reentry'
        identities.append(dict(raw_id=raw_id, old_wire_id=old['request_id'],
                               new_wire_id=new['request_id']))
        if any(d['wire_id'] == old['request_id'] for d in drops):
            reentered_drops.append(raw_id)
    assert reentered_drops, 'No actual idle-dropped generation reentered'
    return dict(raw_ids=len(identities), distinct_generations=True,
                old_reclaimed_before_new_lookup=True,
                dropped_generation_reentry=reentered_drops, identities=identities)


def audit(directory):
    plan = read(directory / 'diagnostic-plan.json')
    cells = list(directory.glob('*/result.json'))
    assert len(cells) == 1
    cell = cells[0].parent
    result, trace = read(cells[0]), read(cell / 'trace.json')
    first = check_requests(read(cell / 'requests.json'), trace)
    for key in ('status_before_engine_stop', 'status_after_engine_stop'):
        check_resources(result[key], stopped=key == 'status_after_engine_stop')
    service_files = list((cell / 'owned-events').glob('owned-service-*.jsonl'))
    assert len(service_files) == 1, 'Expected isolated server evidence'
    drops = [r for r in records((directory / 'handoffs').glob('*.jsonl'))
             if r['event'] == 'idle_pending_dropped']
    service_rows = records(service_files)
    lifecycle = check_events(service_rows, drops)
    assert not result['service_errors'] and result['protection_summary']['shutdown_closed']
    for process in result['protection_summary']['raw']:
        for key in ('pending_handoffs_before_shutdown', 'prepared_blocks_before_shutdown',
                    'inflight_blocks_before_shutdown'):
            assert process[key] == 0
    extra = read(cell / 'config.json')['kv_connector_extra_config']
    grace = extra.get('cachepilot.idle_finalize_grace_seconds', 0)
    assert extra['cachepilot.idle_finalize'] is True
    assert plan.get('idle_grace_seconds', 0) == grace
    mode = result['mode']
    assert plan.get('mode', mode) == mode
    summary = dict(run=directory.name, grace_seconds=grace,
                   mode=mode, kv_cache_bytes=plan['kv_cache_bytes'],
                   requests=len(first), lifecycle=lifecycle, drops=drops,
                   first_h2d_bytes=result['h2d_bytes'], first_d2h_bytes=result['d2h_bytes'],
                   reported_passed=result['passed'],
                   coverage_passed=(result['h2d_bytes'] > 0 and
                                    (mode == 'zero' or
                                     result['protection_summary']['committed_batches'] > 0)))
    if plan.get('repeat_after_idle', False):
        for filename in ('before-repeat.json', 'after-repeat.json'):
            check_resources(read(directory / filename))
        repeat = check_requests(read(directory / 'repeat-requests.json'), trace)
        summary['repeat_requests'] = len(repeat)
        summary['repeat_output_hash_mismatch_ids'] = [
            key for key in first if first[key]['output_sha256'] != repeat[key]['output_sha256']]
        if plan.get('reuse_request_ids', False):
            summary['raw_id_reuse'] = check_raw_id_reuse(first, repeat, service_rows, drops)
    summary['passed'] = summary['coverage_passed'] and result['passed']
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('directories', nargs='+', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    result = dict(scope='Lifecycle and coverage diagnosis; no KV content or performance claim',
                  runs=[audit(p) for p in args.directories])
    args.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
