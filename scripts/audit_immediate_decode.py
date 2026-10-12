"""Independent exact-wire timeline audit of immediate decode diagnostics."""
import argparse
import json
from collections import Counter
from pathlib import Path


def audit(cell):
    result = json.loads((cell / 'result.json').read_text(encoding='utf-8'))
    paths = list((cell / 'owned-events').glob('owned-service-*.jsonl'))
    if len(paths) != 1:
        raise ValueError('Requires one service timeline')
    rows = [json.loads(line) for line in paths[0].read_text(encoding='utf-8').splitlines()]
    before = result['status_before_engine_stop']['generation_observer']
    after = result['status_after_engine_stop']['generation_observer']
    residues = []
    for wire in before['session_ids']:
        timeline = [r for r in rows if r.get('request_id') == wire]
        ends = [r for r in timeline if r['event'] == 'generation_server_end_returned']
        accesses = [r for r in timeline if r['event'] == 'diagnostic_session_access']
        end = ends[-1] if ends else None
        late = [r for r in accesses if end and r['monotonic_ns'] > end['monotonic_ns']]
        residues.append(dict(wire_id=wire, end_returned=end,
            post_end_accesses=late, timeline=timeline,
            end_then_store_resolve=bool(end and not end['session_remains'] and any(
                r['caller'] == 'resolve_obj_keys' for r in late))))
    def token(r):
        return (r['request_id'], r['lock_id'], r['epoch'], r['serial'])
    acquired = Counter(token(r) for r in rows if r['event'] == 'reservation_acquired')
    released = Counter(token(r) for r in rows if r['event'] == 'reservation_released')
    def transfer(r):
        return (r['request_id'], r['sequence'], r['kind'])
    submitted = Counter(transfer(r) for r in rows if r['event'] == 'transfer_submitted')
    retired = Counter(transfer(r) for r in rows if r['event'] == 'transfer_retired')
    terminals = Counter(transfer(r) for r in rows if r['event'] == 'terminal_seen')
    late_store_after_end = []
    for end in [r for r in rows if r['event'] == 'generation_server_end_returned']:
        for store in rows:
            if (store['event'] == 'transfer_submitted' and store['kind'] == 'store'
                    and store['request_id'] == end['request_id']
                    and store['monotonic_ns'] > end['monotonic_ns']):
                late_store_after_end.append(dict(wire_id=end['request_id'],
                    end_monotonic_ns=end['monotonic_ns'], store_monotonic_ns=store['monotonic_ns']))
    return dict(formal=False, original_resource_gate=result['passed'],
        requests=result['requests'], http_errors=result['errors'],
        before=before, after=after, residual_timelines=residues,
        residual_end_then_store_resolve=bool(residues) and all(r['end_then_store_resolve'] for r in residues),
        reservation_closed=acquired == released and all(n == 1 for n in acquired.values()),
        reservation_count=sum(acquired.values()),
        original_native_transfer_closed=bool(submitted) and submitted == retired == terminals and all(n == 1 for n in submitted.values()),
        native_transfer_count=sum(submitted.values()), late_store_after_end=late_store_after_end,
        exact_server_empty=before == after == dict(session_ids=[], owned_job_ids=[], transfer_wire_ids=[]))


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('cell', type=Path)
    p.add_argument('--output', required=True, type=Path)
    a = p.parse_args()
    report = audit(a.cell)
    a.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: v for k, v in report.items() if k not in ('residual_timelines', 'late_store_after_end')}))
