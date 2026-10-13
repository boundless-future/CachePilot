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
from audit_shared_structure_windows import classify


def audit_allocations(windows, allocations):
    checks = dict(
        allocations_observed=bool(allocations) and len(allocations) == len(windows),
        allocation_step_sequence=[r['step'] for r in allocations] == list(range(1, len(allocations)+1)),
        allocation_window_boundaries=len(allocations) == len(windows) and all(
            r['free'] == w['free'] and r['reserve'] == w['reserve']
            and w['monotonic_ns'] < r['monotonic_ns']
            and (i+1 == len(windows) or r['monotonic_ns'] < windows[i+1]['monotonic_ns'])
            for i, (w, r) in enumerate(zip(windows, allocations))),
        allocation_counts=all(r['allocated'] == sum(c['count'] for c in r['calls']) and
            all(type(c['count']) is int and c['count'] >= 0 and
                c['count'] == len(c['block_ids']) == len(set(c['block_ids'])) and
                c['free_before']-c['free_after'] == c['count'] for c in r['calls'])
            for r in allocations),
        physical_pool_bounds=bool(allocations) and len({r['usable_blocks'] for r in allocations}) == 1
            and all(r['usable_blocks'] > 0 and 0 <= r['free'] <= r['usable_blocks']
                    and 0 <= r['free_after'] <= r['usable_blocks'] for r in allocations))
    known = [r for r in allocations if r['reserve'] is not None]
    summary = dict(steps=len(allocations), gross_allocated=sum(r['allocated'] for r in allocations),
        zero_token_allocation_steps=sum(r['scheduled_tokens'] == 0 and r['allocated'] > 0 for r in allocations),
        known_reserve_steps=len(known), reserve_exceeded_steps=sum(r['allocated'] > r['reserve'] for r in known),
        reserve_above_total_pool_steps=sum(r['reserve'] > r['usable_blocks'] for r in known),
        max_reserve=max((r['reserve'] for r in known), default=None),
        max_actual_gross_allocation=max((r['allocated'] for r in allocations), default=None),
        usable_blocks=allocations[0]['usable_blocks'] if allocations else None,
        reserve_vs_actual=[dict(step=r['step'], free=r['free'], reserve=r['reserve'], allocated=r['allocated'],
            scheduled_tokens=r['scheduled_tokens']) for r in allocations])
    return checks, summary


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
        expected.update(kv_connector='SharedStructureAllocationConnector',kv_connector_module_path='scripts.shared_structure_allocation_connector')
        checks['diagnostic_config']=read(cell/'config.json')==expected
        handoffs=rows(cell/'events','handoff-*.jsonl');service=rows(cell/'events','owned-service-*.jsonl')
        generation=audit_rows(handoffs,service,read(cell/'result.json'))
        checks['raw_generation_and_original_receipts']=generation['passed']
        windows=[r for r in handoffs if r['event']=='shared_window']
        checks['window_observed']=bool(windows)
        allocations=[r for r in handoffs if r['event']=='structure_actual_allocation']
        allocation_checks, allocation_summary = audit_allocations(windows, allocations)
        checks.update(allocation_checks)
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
            allocation_summary=allocation_summary,generation=generation,windows=len(windows),candidate_observations=dict(counts),details=details))
    return dict(diagnostic_passed=all(r['diagnostic_passed'] for r in reports),rows=reports,performance=False,
        scope='Read-only actual allocation and windows with event overhead. Gross new allocations do not account for touch/free or prove safe next-step reserve. Counts are repeated candidate observations, not distinct requests. '
              'Visible checks do not prove token-prefix proof or later same-step availability; this separate run cannot explain '
              'the original unobserved pilot causally or replace its zero-coverage failure.')


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('directory',type=Path);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();r=audit(a.directory);a.output.write_text(json.dumps(r,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in r.items() if k!='rows'}))
    raise SystemExit(0 if r['diagnostic_passed'] else 1)
