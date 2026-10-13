"""Independent matrix checks; no aggregate for missing/failed cells."""
import argparse
import json
from pathlib import Path
import statistics
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
sys.path.insert(1,str(Path(__file__).resolve().parents[1]))
from run_shared_strategy_matrix import CASES, configure, order, read, digest, KV_BYTES, VALIDATION_SHA
from run_generation_matrix import extra_checks
from run_reference_baselines import resource_gate
from audit_replay_measurements import audit_cell as timing_audit
import run_protection_ablation as ablation


def audit_one(cell,name,repeat,signature,trace):
    case=CASES[name]
    result,manifest=read(cell/'result.json'),read(cell/'run-manifest.json')
    snapshot=read(cell/'generation-summary.json')
    summary=ablation.merge_protection_summary(cell/'protection-summary')
    logs=ablation.service_error_report(cell,read(cell/'shutdown-context.json'))
    timing=timing_audit(cell)
    requests=read(cell/'requests.json')['results']
    generations=snapshot['generations'];expected=len(trace['events'])+1
    checks=dict(extra_checks(cell,case,result,trace),
        result=result['passed'],resources=resource_gate('lazy',result),
        config=read(cell/'config.json')==configure(read(ablation.ROOT/'configs/protection-zero.json'),case),
        source=manifest['source_sha256']==signature['source_sha256'],
        trace=digest(read(cell/'trace.json'))==digest(trace)==manifest['trace_sha256']==signature['trace_sha256']==VALIDATION_SHA,
        budget=manifest['kv_cache_bytes']==signature['kv_cache_bytes']==KV_BYTES,
        mode=manifest['mode']==result['mode']==case['mode'],repeat=manifest['repeat']==result['repeat']==repeat,
        healthy=snapshot.get('protection_poisoned') is None and not snapshot.get('fault_error') and not snapshot.get('shutdown_error'),
        receipts=summary['committed_batches']==summary['receipts'] and summary['failed_receipts']==0,
        coverage=case['mode']=='zero' or summary['committed_batches']>0,
        bounded=summary['peak_pinned_blocks']<={'zero':0,'one':16,'two':32}[case['mode']],
        protection_closed=summary['shutdown_closed'],h2d=result['h2d_bytes']>0,
        fatal_logs=not logs['fatal_errors'],raw_timing=timing['passed'],
        generations=len(generations)==len({g['wire_id'] for g in generations})==expected and all(
            summary['events'].get(k)==expected for k in ('generation_registered','generation_end_submitted')))
    return dict(passed=all(checks.values()),checks=checks,ttft_p95_ms=result['ttft_ms']['p95'],
        h2d_bytes=result['h2d_bytes'],d2h_bytes=result['d2h_bytes'],committed=summary['committed_batches'],
        receipts=summary['receipts'],peak_pinned_blocks=summary['peak_pinned_blocks'],fallbacks=summary['fallbacks'],
        prefill_tokens=result['vllm_delta'].get('vllm:request_prefill_kv_computed_tokens_sum'),
        external_hit_tokens=result['vllm_delta'].get('vllm:external_prefix_cache_hits_total'),
        generation_count=len(generations),shutdown_teardown_errors=logs['shutdown_teardown_errors'],
        timing_errors=timing['errors'],output_hashes={str(r['event_id']):r['output_sha256'] for r in requests})


def aggregate(rows):
    if (len(rows)!=12 or {(r['case'],r['repeat']) for r in rows} != {(n,i) for n in CASES for i in range(3)}
            or not all(r['passed'] for r in rows)):return {}
    report={}
    for name in CASES:
        points=sorted((r for r in rows if r['case']==name),key=lambda r:r['repeat'])
        report[name]={}
        for metric in ('ttft_p95_ms','h2d_bytes','d2h_bytes','prefill_tokens','committed'):
            values=[r[metric] for r in points]
            report[name][metric]=dict(mean=statistics.mean(values),stdev=statistics.stdev(values),individual=values)
    baseline=report['selected-zero']['ttft_p95_ms']['mean']
    for name in CASES:report[name]['relative_selected_zero_pct']=100*(report[name]['ttft_p95_ms']['mean']/baseline-1)
    return report


def audit(directory):
    directory=Path(directory);plan,trace=read(directory/'plan.json'),read(directory/'trace.json')
    repeats=1 if plan['stage']=='pilot' else 3
    if (plan['stage'] not in ('pilot','formal') or plan['repeats']!=repeats or plan['cases']!=CASES
            or digest(trace)!=VALIDATION_SHA or plan['orders']!=[order(i) for i in range(repeats)]
            or plan['signature']['cases']!=CASES or plan['signature']['hotpath_diagnostics'] is not False):
        raise ValueError('Frozen shared-prefix matrix differs')
    rows,differences=[],[]
    for repeat in range(repeats):
        for name in CASES:
            try:row=audit_one(directory/f'{name}-r{repeat}',name,repeat,plan['signature'],trace)
            except (OSError,ValueError,KeyError,TypeError,AssertionError) as exc:row=dict(passed=False,error=repr(exc))
            rows.append(dict(case=name,repeat=repeat,**row))
        baseline=next(r for r in rows if r['repeat']==repeat and r['case']=='selected-zero')
        for row in [r for r in rows if r['repeat']==repeat]:
            if 'output_hashes' in baseline and 'output_hashes' in row:
                differences.append(dict(case=row['case'],repeat=repeat,eligible=row['passed'] and baseline['passed'],
                    event_ids=[k for k,v in baseline['output_hashes'].items() if row['output_hashes'].get(k)!=v]))
    return dict(matrix_complete=all(r['passed'] for r in rows),passed_cells=sum(r['passed'] for r in rows),
        rows=rows,aggregates=aggregate(rows) if plan['stage']=='formal' else {},output_differences=differences,
        scope='Historical synthetic input; pilot excluded from formal; sample SD is not confidence interval')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('directory',type=Path);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();result=audit(args.directory)
    args.output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:result[k] for k in ('matrix_complete','passed_cells','aggregates')}))
    raise SystemExit(0 if result['matrix_complete'] else 1)
