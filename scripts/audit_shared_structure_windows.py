"""Window observations explain this diagnostic only, not the earlier pilot."""
from collections import Counter
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
sys.path.insert(1,str(Path(__file__).resolve().parents[1]))
from audit_shared_structure_matrix import audit_one
from run_shared_structure_matrix import CASES, configure, read
from audit_generation_pilot import rows, audit_rows


def classify(window, candidate):
    if candidate['live'] or not candidate['finished'] or candidate['inflight'] or candidate['protected']:
        return 'request_ineligible'
    reserve=window['reserve'];left=window['budget']-window['used']
    if reserve is None:return 'unknown_reserve'
    if window['free'] < reserve+16:return 'minimum_capacity'
    if not any(b['tracked'] and b['rank'] is not None and b['rank']<reserve for b in candidate['blocks']):
        return 'outside_pressure_ranks'
    if candidate['start']!=0 or candidate['end']<256 or candidate['end']%256 or candidate['skip']:
        return 'metadata_ineligible'
    ids=[b['id'] for b in candidate['blocks']]
    if len(candidate['block_ids'])!=1 or len(ids)!=(candidate['end']-candidate['start'])//16 or len(set(ids))!=len(ids):
        return 'layout_ineligible'
    if left<16:return 'inflight_budget'
    n=min(len(ids),left)
    if window['free']-n<reserve:return 'selected_budget_capacity'
    if any(b['refs']<0 or b['null'] or not b['hash_matches'] or not b['tracked'] for b in candidate['blocks'][:n]):
        return 'block_or_hash_ineligible'
    return 'visible_checks_satisfied'


def audit(directory):
    directory=Path(directory);plan=read(directory/'plan.json');trace=read(directory/'trace.json')
    from audit_real_structure import audit as provenance_audit
    provenance=provenance_audit(directory/'input/built',directory/'input/source',directory/'input/catalogue.json')
    assert provenance==read(directory/'provenance-audit.json') and trace==read(directory/'input/built/trace.json')
    reports=[]
    for name in ('shared-one','shared-two'):
        cell=directory/name
        base=audit_one(cell,name,0,plan['signature'],trace)
        # These differ by explicit design; retain all original checks separately.
        checks={k:v for k,v in base['checks'].items() if k not in ('result','coverage','config','no_hotpath_jsonl')}
        expected=configure(read(Path(__file__).resolve().parents[1]/'configs/protection-zero.json'),CASES[name])
        expected.update(kv_connector='SharedStructureWindowConnector',kv_connector_module_path='scripts.shared_structure_window_connector')
        checks['diagnostic_config']=read(cell/'config.json')==expected
        handoffs=rows(cell/'events','handoff-*.jsonl');service=rows(cell/'events','owned-service-*.jsonl')
        generation=audit_rows(handoffs,service,read(cell/'result.json'))
        checks['raw_generation_and_original_receipts']=generation['passed']
        windows=[r for r in handoffs if r['event']=='shared_window']
        checks['window_observed']=bool(windows)
        counts=Counter();details=[]
        for w in windows:
            for c in w['pending']:
                reason=classify(w,c);counts[reason]+=1
                if reason!='request_ineligible':
                    details.append(dict(monotonic_ns=w['monotonic_ns'],wire_id=c['wire_id'],prefix_sha256=c['prefix_sha256'],
                        reason=reason,free=w['free'],reserve=w['reserve'],budget=w['budget'],used=w['used'],
                        metadata_tokens=c['end']-c['start'],shared_blocks=sum(b['refs']>0 for b in c['blocks'])))
        reports.append(dict(case=name,diagnostic_passed=all(checks.values()),checks=checks,
            performance_coverage=base['checks']['coverage'],committed=base['committed'],fallbacks=base['fallbacks'],
            generation=generation,windows=len(windows),candidate_observations=dict(counts),details=details))
    return dict(diagnostic_passed=all(r['diagnostic_passed'] for r in reports),rows=reports,performance=False,
        scope='Read-only windows with event logging overhead. Counts are repeated candidate observations, not distinct requests. '
              'Visible checks do not prove token-prefix proof or later same-step availability; this separate run cannot explain '
              'the original unobserved pilot causally or replace its zero-coverage failure.')


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('directory',type=Path);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();r=audit(a.directory);a.output.write_text(json.dumps(r,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in r.items() if k!='rows'}))
    raise SystemExit(0 if r['diagnostic_passed'] else 1)
