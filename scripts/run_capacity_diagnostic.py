"""Two fixed-input diagnostic runs; observed timings are not performance data."""
import argparse
import json
import os
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
sys.path.insert(1,str(Path(__file__).resolve().parents[1]))
import run_protection_ablation as ablation
import validate_environment as env
from run_generation_strategy_pilot import read,KV_BYTES,strict_idle,TRACE_SHA,digest
from audit_generation_strategy_validation import VALIDATION_SHA,input_audit
from audit_capacity_diagnostic import config,audit_cell,audit

ADDITIONS={'scripts/capacity_diagnostic_protection.py','scripts/capacity_diagnostic_connector.py',
           'scripts/run_capacity_diagnostic.py','scripts/audit_capacity_diagnostic.py'}


def main():
    p=argparse.ArgumentParser();p.add_argument('--prerequisite',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    if Path(ablation.__file__).resolve()!=Path(__file__).with_name('run_protection_ablation.py').resolve():
        raise RuntimeError('Measurement helper path mismatch')
    previous=read(args.prerequisite/'plan.json')['signature']['source_sha256'];sources=ablation.source_sha()
    if any(sources.get(k)!=v for k,v in previous.items()) or set(sources)-set(previous)!=ADDITIONS:
        raise ValueError('Only four diagnostic additions allowed; frozen runtime must not change')
    traces={'development':read(args.prerequisite/'development-trace.json'),'validation':read(args.prerequisite/'trace.json')}
    if not input_audit(traces['development'],traces['validation'])['passed']:raise ValueError('Input mismatch')
    ablation.ports_free();out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    signature=dict(source_sha256=sources,kv_cache_bytes=KV_BYTES)
    env.write_json(out/'plan.json',dict(signature=signature,order=list(traces),diagnostic=True,
        prerequisite=str(args.prerequisite),scope='Fixed selected-one h5/d1, unchanged reserve; no performance claim'))
    class Service(env.Service):
        def start(self,health,timeout=900):
            os.environ['CACHEPILOT_OWNED_EVENT_LOG']='0'
            if self.log.name=='lmcache.log':
                self.command=[sys.executable,str(ablation.ROOT/'scripts/generation_service_observer.py'),
                    'server','--host','127.0.0.1','--port','5555','--http-host','127.0.0.1','--http-port','8080',
                    '--l1-size-gb','16','--l1-init-size-gb','16','--eviction-policy','LRU']
            else:
                i=self.command.index('--kv-transfer-config')+1;c=config(json.loads(self.command[i]));self.command[i]=json.dumps(c)
                env.write_json(self.log.parent/'config.json',c);env.write_json(self.log.parent/'launch-command.json',self.command)
                os.environ['CACHEPILOT_CAPACITY_DIAGNOSTICS']='1'
                os.environ['CACHEPILOT_HANDOFF_DIR']=str(self.log.parent/'handoff')
                os.environ['CACHEPILOT_GENERATION_SUMMARY_PATH']=str(self.log.parent/'generation-summary.json')
            return super().start(health,timeout)
    ablation.Service=Service
    original=ablation.cache_drained;ablation.cache_drained=lambda s:original(s) and strict_idle(s)
    for name,trace in traces.items():
        if ablation.source_sha()!=sources:raise RuntimeError('Frozen source changed')
        ablation.ports_free();cell=out/name;print('START '+name,flush=True)
        try:
            try:ablation.run_cell(cell,'one',0,trace,str(ablation.ROOT/'models/Qwen3-4B'),KV_BYTES)
            except RuntimeError as exc:
                if str(exc)!=f'cell failed resource or replay gate: {cell}':raise
            row=audit_cell(cell,signature,TRACE_SHA if name=='development' else VALIDATION_SHA)
            env.write_json(cell/'diagnostic-audit.json',row)
            if not row['passed']:raise RuntimeError('Diagnostic gates failed')
        except BaseException as exc:
            env.write_json(out/'stopped.json',dict(cell=name,error=repr(exc)));env.write_json(out/'summary.json',audit(out));raise
        env.write_json(out/'summary.json',audit(out));print('DONE '+name,flush=True)


if __name__=='__main__':main()
