"""Single lifecycle-timeline observation of the long-prefill idle identity boundary."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path


TRACE_SHA = 'd15108f305834554a7cd3cbe7da5103ee3a8348e587594300db5bf37d29e3cd0'


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--prerequisite',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--stop-starting-at',required=True)
    args=parser.parse_args()
    cutoff=datetime.fromisoformat(args.stop_starting_at)
    if cutoff.utcoffset() is None or datetime.now(timezone.utc)>=cutoff:
        parser.error('A future timezone-aware start cutoff is required')
    import run_protection_ablation as ablation
    from run_fixed_baselines import configure, CASES, KV_BYTES, read, strict_idle
    from run_arrival_matrix import digest
    from validate_environment import Service, write_json
    from audit_replay_measurements import audit_cell
    prior=read(args.prerequisite/'h2p5-d64-r0/result.json')
    trace=read(args.prerequisite/'trace.json')
    if (digest(trace)!=TRACE_SHA or prior['passed'] or not any(
            'Idle pending differs from current wire identity' in row['text']
            for row in prior.get('service_errors',[]))):
        raise ValueError('Requires the preserved long-prefill identity counterexample')
    frozen=read(args.prerequisite/'plan.json')['signature']['source_sha256']
    current=ablation.source_sha()
    if any(current.get(key)!=value for key,value in frozen.items()):
        raise ValueError('An existing source changed since the failed pilot')
    out=args.output.resolve()
    ablation.ports_free()
    class ProbeService(Service):
        def start(self,health,timeout=900):
            if self.log.name=='vllm.log':
                os.environ['CACHEPILOT_IDLE_IDENTITY_DIR']=str(out/'identity-observation')
                i=self.command.index('--kv-transfer-config')+1
                config=configure(json.loads(self.command[i]),CASES['h2p5-d64'])
                config.update(kv_connector='IdentityTimelineConnector',
                    kv_connector_module_path='scripts.identity_timeline_connector')
                self.command[i]=json.dumps(config)
                write_json(self.log.parent/'config.json',config)
            return super().start(health,timeout)
    ablation.Service=ProbeService
    original_drained=ablation.cache_drained
    ablation.cache_drained=lambda state: original_drained(state) and strict_idle(state)
    caught=None
    try:
        ablation.run_cell(out,'zero',0,trace,str(ablation.ROOT/'models/Qwen3-4B'),KV_BYTES)
    except Exception as exc:
        caught=repr(exc)
    result=read(out/'result.json')
    path=out/'identity-observation/identity-failure.jsonl'
    observations=[json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
    report=dict(observation_captured=bool(observations),observations=observations,caught=caught,
        original_performance_gate_passed=result['passed'],
        measurements=audit_cell(out),source_matches_prerequisite=True,
        scope='Unchanged exception and resource gate; no latency aggregation; missing observation is not evidence of repair')
    report['timeline_rows']={p.name:sum(1 for _ in p.open()) for p in (out/'identity-observation').glob('timeline-*.jsonl')}
    write_json(out/'identity-diagnostic.json',report)
    ablation.ports_free()
    print(json.dumps({key:report[key] for key in ('observation_captured','original_performance_gate_passed')}))
    if not report['timeline_rows'] or not report['measurements']['passed']:
        raise SystemExit(1)


if __name__=='__main__':
    main()
