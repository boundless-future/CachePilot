"""Account actual native demand at each call, without counterfactual claims."""
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(1, str(Path(__file__).resolve().parents[1]))
from audit_shared_structure_allocation import audit as allocation_audit
from audit_generation_pilot import rows
from run_shared_structure_matrix import CASES, configure, read


def audit_calls(calls, steps):
    successful = [r for r in calls if r['success']]
    rejected = [r for r in calls if not r['success']]
    by_step = {r['step']: r for r in steps}
    checks = dict(native_calls_observed=bool(calls),
        native_serial_sequence=[r['serial'] for r in calls] == list(range(1, len(calls)+1)),
        native_no_call_error=all(r['error'] is None for r in calls),
        native_step_link=all(r['step'] in by_step and r['full_reserve'] == by_step[r['step']]['reserve']
            and r['monotonic_ns'] < by_step[r['step']]['monotonic_ns'] for r in calls),
        native_no_unaccounted_gross=all(sum(r['new_blocks'] for r in calls if r['step']==s['step']) == s['allocated'] for s in steps),
        native_full_attention_consumption=all(r['free_before']-r['free_after'] == r['new_blocks']+r['touched_free_blocks'] for r in calls),
        native_successful_demand=all(r['demands'] and r['demands'][-1]['required'] == r['new_blocks']+r['touched_free_blocks']
            and r['demands'][-1]['required']+r['reserved_blocks']+r['watermark'] <= r['demands'][-1]['free'] for r in successful),
        native_rejection_no_allocation=all(not r['new_blocks'] and not r['touched_free_blocks'] for r in rejected))
    rejection_gates=[]
    for r in rejected:
        if not r['demands']:
            rejection_gates.append(False)
        elif r['full_sequence_must_fit'] and len(r['demands']) == 1:
            rejection_gates.append(r['demands'][0]['required']+r['watermark'] > r['demands'][0]['free'])
        else:
            d=r['demands'][-1]
            rejection_gates.append(d['required']+r['reserved_blocks']+r['watermark'] > d['free'])
    checks['native_rejection_capacity_gate'] = all(rejection_gates)
    summary=dict(calls=len(calls), successful=len(successful), rejected=len(rejected),
        gross_new_blocks=sum(r['new_blocks'] for r in calls),
        zero_ref_touch_consumption=sum(r['touched_free_blocks'] for r in calls),
        zero_compute_calls_with_new_blocks=sum(r['num_new_tokens']==0 and r['new_blocks']>0 for r in calls),
        external_load_calls=sum(r['num_external_computed_tokens']>0 for r in calls),
        native_reserved_calls=sum(r['reserved_blocks']>0 for r in calls),
        watermark_calls=sum(r['watermark']>0 for r in calls),
        positive_touch_calls=sum(r['touched_free_blocks']>0 for r in calls),
        scope='Single-call observations; native demand includes zero-ref GPU hit touches. No safe extra-pin or whole-step prediction.')
    return checks, summary


def audit(directory):
    directory=Path(directory)
    result=allocation_audit(directory)
    for row in result['rows']:
        cell=directory/row['case']
        expected=configure(read(Path(__file__).resolve().parents[1]/'configs/protection-zero.json'), CASES[row['case']])
        expected.update(kv_connector='SharedStructureNativeConnector', kv_connector_module_path='scripts.shared_structure_native_connector')
        row['checks']['diagnostic_config']=read(cell/'config.json')==expected
        events=rows(cell/'events','handoff-*.jsonl')
        calls=[r for r in events if r['event']=='native_allocation_call']
        steps=[r for r in events if r['event']=='structure_actual_allocation']
        checks,summary=audit_calls(calls,steps)
        row['checks'].update(checks)
        row['native_summary']=summary
        row['diagnostic_passed']=all(row['checks'].values())
    result['diagnostic_passed']=all(r['diagnostic_passed'] for r in result['rows'])
    return result


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(); p.add_argument('directory',type=Path); p.add_argument('--output',type=Path,required=True)
    a=p.parse_args(); report=audit(a.directory)
    a.output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(diagnostic_passed=report['diagnostic_passed'])))
    raise SystemExit(0 if report['diagnostic_passed'] else 1)
