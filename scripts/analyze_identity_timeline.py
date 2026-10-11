"""Offline lifecycle diagnosis; never promotes a failed cell to passed."""
import argparse
import json
from pathlib import Path


def analyze(directory):
    directory = Path(directory)
    evidence = directory / 'identity-observation'
    def rows(name):
        return [json.loads(line) for line in (evidence/name).read_text().splitlines()]
    scheduler = rows('timeline-SCHEDULER.jsonl')
    worker = rows('timeline-WORKER.jsonl')
    result = json.loads((directory/'result.json').read_text())
    assert scheduler[-1]['event'] == 'before_shutdown', 'Incomplete scheduler trace'
    for stream in (scheduler, worker):
        assert len({row['pid'] for row in stream}) == 1
        assert all(a['monotonic_ns'] <= b['monotonic_ns'] for a,b in zip(stream,stream[1:]))
        assert not any(row['event'].endswith('_error') for row in stream)
    lookups = {r['wire_id'] for r in scheduler if r['event']=='lookup_after'}
    ended = {r['current'] for r in scheduler if r['event']=='end_session_before'}
    stores = {r['wire_id'] for r in worker if r['event']=='store_after'}
    created = {r['wire_id']:r for r in scheduler if r['event']=='tracker_created'}
    candidates = []
    for wire in sorted(stores-lookups):
        origin = created[wire]
        rid = origin['request_id']
        admissions = [r for r in scheduler if r['event']=='add_store_candidate_after'
                      and r.get('wire_id')==wire]
        assert origin['request_status']=='PREEMPTED' and origin['replaced']
        assert admissions and origin['monotonic_ns'] < admissions[0]['monotonic_ns']
        reset = [r for r in scheduler if r['event']=='on_request_reset_after'
                 and r.get('request_id')==rid and r['monotonic_ns']<origin['monotonic_ns']]
        assert reset
        mappings = [{url:ids.get(rid) for url,ids in r['mappings'].items()}
                    for r in admissions]
        assert all(mapping and all(value != wire for value in mapping.values())
                   for mapping in mappings)
        candidates.append(dict(request_id=rid, store_wire=wire,
            reset_ns=reset[-1]['monotonic_ns'], tracker_ns=origin['monotonic_ns'],
            first_admission_ns=admissions[0]['monotonic_ns'],
            admission_mappings=mappings, ended=wire in ended))
    assert candidates, 'No STORE-only preempted identity observed'
    return dict(diagnostic_chain_observed=True, original_cell_passed=result['passed'],
        requests=result['requests'], natural_preemptions=result['vllm_delta']['vllm:num_preemptions_total'],
        lookup_identities=len(lookups), store_identities=len(stores),
        stores_without_lookup=candidates,
        unended_store_identities=sorted(stores-ended),
        active_sessions_before_stop=result['status_before_engine_stop']['active_sessions'],
        idle_exception_reproduced=any('Idle pending differs' in r['text']
                                     for r in result['service_errors']),
        scope='Client dispatch ordering only; END return is not server acknowledgement. '
              'Matching residual count does not identify server sessions. No performance aggregation.')


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('directory',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    args.output.write_text(json.dumps(analyze(args.directory),indent=2)+'\n',encoding='utf-8')
