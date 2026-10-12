"""Recompute diagnostic reserve and candidate windows, without performance claims."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(1, str(Path(__file__).resolve().parents[1]))
from run_generation_strategy_pilot import read, digest, CASES, strategy_config, KV_BYTES
from audit_generation_strategy_validation import VALIDATION_SHA
from run_generation_strategy_pilot import TRACE_SHA
from run_fixed_baselines import token_gate
from run_reference_baselines import resource_gate
from run_generation_matrix import generation_closed
from audit_replay_measurements import audit_cell as timing_audit
import run_protection_ablation as ablation


def config(original):
    value = strategy_config(original, CASES['selected-one'])
    value.update(kv_connector='CapacityDiagnosticConnector',
        kv_connector_module_path='scripts.capacity_diagnostic_connector')
    return value


def audit_cell(cell, signature, expected_digest):
    result, trace = read(cell/'result.json'), read(cell/'trace.json')
    manifest = read(cell/'run-manifest.json')
    summary = ablation.merge_protection_summary(cell/'protection-summary')
    generation = read(cell/'generation-summary.json')
    logs = ablation.service_error_report(cell, read(cell/'shutdown-context.json'))
    events = [json.loads(line) for p in sorted((cell/'handoff').glob('*.jsonl'))
              for line in p.read_text().splitlines() if line.strip()]
    boundaries = {e['diagnostic_step']:e for e in events if e.get('stage') == 'boundary'}
    errors, candidates = [], {}
    for step,e in boundaries.items():
        live=e['live']
        reserve=(2*sum((max(r['tokens'],r['computed'])+16)//16 for r in live)+e['watermark']+16
                 if live and e['max_scheduled_tokens']>0 and not any(r['unsupported'] for r in live) else None)
        if reserve != e['reserve']: errors.append(dict(step=step, reason='reserve mismatch'))
        for p in e['pending']:
            key=(p['wire_id'],p['start'],p['end'])
            row=candidates.setdefault(key, dict(wire_id=p['wire_id'], start=p['start'], end=p['end'],
                request_id=p['request_id'], steps=[], margins=[], states=[], terminal_events=[]))
            row['steps'].append(step)
            if reserve is not None: row['margins'].append(e['free']-reserve-16)
    for e in events:
        if e.get('stage') == 'pending_state':
            key=(e['wire_id'],e['start'],e['end'])
            if key not in candidates: errors.append(dict(reason='state without boundary', key=key))
            else:
                candidates[key]['states'].append(dict(step=e['diagnostic_step'],live=e['live'],finished=e['finished'],
                    native_inflight=e['native_inflight'],protected_inflight=e['protected_inflight'],index=e['index']))
        if e['event']=='protection_capacity_diagnostic':
            boundary=boundaries.get(e['diagnostic_step'])
            if (boundary is None or e['reserve']!=boundary['reserve'] or
                    e['free_minus_reserve_minus_chunk']!=e['free']-e['reserve']-16):
                errors.append(dict(reason='capacity observation mismatch'))
        if e['event'] in ('scheduler_built','scheduler_receipt','protection_metadata_committed','protection_receipt'):
            for row in candidates.values():
                if e.get('wire_id')==row['wire_id']:
                    row['terminal_events'].append({k:e[k] for k in ('event','start','end','batch_id','source') if k in e})
    for row in candidates.values():
        row.update(first_step=min(row['steps']),last_step=max(row['steps']),observed_steps=len(row['steps']),
            minimum_margin=min(row['margins']) if row['margins'] else None,
            maximum_margin=max(row['margins']) if row['margins'] else None)
    known_coverage_error=f"RuntimeError('cell failed resource or replay gate: {cell.resolve()}')"
    # Raw errors carry the server's path when audited offline.
    coverage_only=(not result['protection_observed'] and result.get('error','').startswith(
        "RuntimeError('cell failed resource or replay gate: ") and result.get('error','').endswith(cell.name+"')"))
    checks=dict(trace=digest(trace)==expected_digest==manifest['trace_sha256'],
        source=manifest['source_sha256']==signature['source_sha256'],
        config=read(cell/'config.json')==config(read(ablation.ROOT/'configs/protection-zero.json')),
        budget=manifest['kv_cache_bytes']==KV_BYTES, tokens=token_gate(cell,trace),
        timing=timing_audit(cell)['passed'], resources=resource_gate('lazy',result),
        empty=all(s.get('generation_observer')==dict(session_ids=[],owned_job_ids=[],transfer_wire_ids=[])
            for s in (result['status_before_engine_stop'],result['status_after_engine_stop'])),
        generations=generation_closed(generation) and len(generation['generations'])==69
            and all(summary['events'].get(k)==69 for k in ('generation_registered','generation_end_submitted')),
        receipts=summary['committed_batches']==summary['receipts'] and summary['failed_receipts']==0,
        closed=summary['shutdown_closed'],bounded=summary['peak_pinned_blocks']<=16,
        h2d=result['h2d_bytes']>0, fatal=not logs['fatal_errors'],
        result=result['passed'] or coverage_only, requests=result['errors']==0,
        diagnostic=bool(boundaries) and bool(candidates) and not errors)
    return dict(passed=all(checks.values()),checks=checks, errors=errors,
        boundaries=len(boundaries), candidates=list(candidates.values()),
        committed=summary['committed_batches'],fallbacks=summary['fallbacks'],
        capacity_observations=sum(e['event']=='protection_capacity_diagnostic' for e in events),
        teardown=logs['shutdown_teardown_errors'],
        scope='Diagnostic admission permits zero coverage; original performance pilot remains failed. No latency claim. '
              'Wire-level terminal events may include split ranges; inspect range/batch rather than inferring one-to-one identity.')


def audit(directory):
    plan=read(directory/'plan.json')
    rows=[]
    for name,sha in [('development',TRACE_SHA),('validation',VALIDATION_SHA)]:
        try: row=audit_cell(directory/name,plan['signature'],sha)
        except (OSError,ValueError,KeyError,TypeError) as exc: row=dict(passed=False,error=repr(exc))
        rows.append(dict(case=name,**row))
    return dict(passed=all(r['passed'] for r in rows),rows=rows,scope='Two diagnostic cells; no performance aggregation')


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('directory',type=Path);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();r=audit(args.directory);args.output.write_text(json.dumps(r,indent=2)+'\n')
    print(json.dumps(dict(passed=r['passed'],cells=[{k:v for k,v in x.items() if k not in ('candidates',)} for x in r['rows']])))
    raise SystemExit(0 if r['passed'] else 1)
