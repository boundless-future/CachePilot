"""One preregistered read-only physical-allocation diagnostic, never performance."""
import argparse
import json
import os
from pathlib import Path
import sys
import tarfile
import shutil
from datetime import datetime, timezone
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(1, str(Path(__file__).resolve().parents[1]))
from run_generation_strategy_pilot import strategy_config, KV_BYTES, strict_idle, read, digest
from run_real_structure_pilot import TRACE_SHA as VALIDATION_SHA, validate_inputs

CASES = {
    'default-zero': dict(kind='lazy', horizon=2.5, drain=64, mode='zero'),
    'selected-zero': dict(kind='lazy', horizon=5.0, drain=1, mode='zero'),
    'shared-one': dict(kind='lazy', horizon=5.0, drain=1, mode='one'),
    'shared-two': dict(kind='lazy', horizon=5.0, drain=1, mode='two'),
}
ADDITIONS = {'scripts/run_shared_structure_matrix.py', 'scripts/audit_shared_structure_matrix.py'}


def configure(original, case):
    config = strategy_config(original, case)
    config.update(kv_connector='SharedPrefixConnector',
                  kv_connector_module_path='scripts.shared_prefix_connector')
    config['kv_connector_extra_config']['cachepilot.shared_prefix_protection'] = True
    return config


def order(repeat):
    names = list(CASES)
    return names[repeat:] + names[:repeat]


def check_admission_sources(previous, current):
    if any(current.get(p) != sha for p, sha in previous.items()) or set(current)-set(previous) != ADDITIONS:
        raise ValueError('Only two structure harness additions permitted')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--stage', choices=('pilot', 'formal'), required=True)
    parser.add_argument('--admission', type=Path)
    parser.add_argument('--pilot', type=Path)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--stop-starting-at', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    cutoff = datetime.fromisoformat(args.stop_starting_at)
    if cutoff.utcoffset() is None or datetime.now(timezone.utc) >= cutoff:
        parser.error('Future timezone-aware cutoff required')
    import run_protection_ablation as ablation
    import validate_environment as env
    from audit_shared_structure_allocation import audit
    if Path(ablation.__file__).resolve() != Path(__file__).with_name('run_protection_ablation.py').resolve():
        raise RuntimeError('Wrong measurement helper')
    sources = ablation.source_sha()
    prerequisite = {}
    provenance = validate_inputs(args.input/'built', args.input/'source', args.input/'catalogue.json', ablation.ROOT/'models/Qwen3-4B')
    if args.stage != 'pilot' or args.admission is None:
        parser.error('Diagnostic requires --stage pilot --admission completed-window-diagnostic')
    prior = read(args.admission/'plan.json')
    from audit_shared_structure_windows import audit as pilot_audit
    previous = pilot_audit(args.admission)
    if not previous['diagnostic_passed']:
        raise ValueError('Requires completed window diagnostic with intact evidence')
    additions={'scripts/shared_structure_allocation_connector.py','scripts/run_shared_structure_allocation.py','scripts/audit_shared_structure_allocation.py'}
    old=prior['signature']['source_sha256']
    if any(sources.get(p)!=sha for p,sha in old.items()) or set(sources)-set(old)!=additions:
        raise ValueError('Runtime changed beyond three allocation diagnostic additions')
    prerequisite=dict(window_diagnostic=str(args.admission))
    trace=read(args.input/'built/trace.json')
    if digest(trace) != VALIDATION_SHA:
        raise ValueError('Historical input changed')
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(ablation.ROOT/'models/Qwen3-4B')
    lengths = [len(e['prompt']) for e in trace['events']]
    if any(not isinstance(e['prompt'], list) or any(type(t) is not int or not 0 <= t < len(tokenizer) for t in e['prompt']) for e in trace['events']):
        raise ValueError('Anonymous token IDs invalid')
    if lengths != [e['prompt_tokens'] for e in trace['events']]:
        raise ValueError('Actual tokenizer lengths differ')
    ablation.ports_free()
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    repeats=1 if args.stage=='pilot' else 3
    signature=dict(source_sha256=sources,trace_sha256=VALIDATION_SHA,kv_cache_bytes=KV_BYTES,
                   cases=CASES,gate='shared-prefix-structure-allocation-v1',hotpath_diagnostics=True)
    env.write_json(out/'plan.json',dict(stage=args.stage,repeats=repeats,cases=CASES,signature=signature,
        orders=[['shared-one','shared-two']],prerequisite=prerequisite,
        scope='Separate read-only gross allocation diagnostic, never performance or a safe reserve proof', performance=False, stop_starting_at=args.stop_starting_at))
    shutil.copytree(args.input, out/'input')
    env.write_json(out/'provenance-audit.json', provenance)
    env.write_json(out/'trace.json',trace)
    env.write_json(out/'tokenizer-audit.json',dict(passed=True,lengths=lengths,total=sum(lengths)))
    with tarfile.open(out/'frozen-source.tar.gz','w:gz') as archive:
        for name in sources:archive.add(ablation.ROOT/name,arcname=name)
    current=None
    class Service(env.Service):
        def start(self,health,timeout=900):
            os.environ.update(CACHEPILOT_OWNED_EVENT_LOG='1', CACHEPILOT_OWNED_SERVICE_DIR=str(cell/'events'), CACHEPILOT_HANDOFF_DIR=str(cell/'events'))
            if self.log.name=='lmcache.log':
                self.command=[sys.executable,str(ablation.ROOT/'scripts/generation_service_observer.py'),
                    'server','--host','127.0.0.1','--port','5555','--http-host','127.0.0.1','--http-port','8080',
                    '--l1-size-gb','16','--l1-init-size-gb','16','--eviction-policy','LRU','--enable-extra-logging']
            else:
                i=self.command.index('--kv-transfer-config')+1
                config=configure(json.loads(self.command[i]),current)
                config.update(kv_connector='SharedStructureAllocationConnector',kv_connector_module_path='scripts.shared_structure_allocation_connector')
                self.command[i]=json.dumps(config)
                env.write_json(self.log.parent/'config.json',config)
                env.write_json(self.log.parent/'launch-command.json',self.command)
                os.environ['CACHEPILOT_GENERATION_SUMMARY_PATH']=str(self.log.parent/'generation-summary.json')
            return super().start(health,timeout)
    ablation.Service=Service
    original=ablation.cache_drained
    ablation.cache_drained=lambda state:original(state) and strict_idle(state)
    for name in ('shared-one','shared-two'):
        if datetime.now(timezone.utc)>=cutoff:
            env.write_json(out/'stopped.json',dict(reason='deadline',next_cell=name));return
        if ablation.source_sha()!=sources:raise RuntimeError('Source drift')
        ablation.ports_free();current=CASES[name];cell=out/name
        print('START '+name,flush=True)
        try:
            ablation.run_cell(cell,current['mode'],0,trace,str(ablation.ROOT/'models/Qwen3-4B'),KV_BYTES)
        except RuntimeError as exc:
            result=read(cell/'result.json')
            # Zero coverage is a diagnostic observation, never promoted to performance success.
            if (not result.get('protection_observed') and result.get('resource_drained')
                    and result.get('errors')==0 and result.get('retrieval_observed')
                    and not result.get('service_errors') and result.get('protection_summary',{}).get('shutdown_closed')):
                env.write_json(cell/'coverage-failure-retained.json',dict(error=repr(exc),performance_passed=False))
            else:
                env.write_json(out/'stopped.json',dict(cell=name,error=repr(exc)));raise
        print('DONE '+name,flush=True)
    result=audit(out);env.write_json(out/'audit.json',result)
    if not result['diagnostic_passed']:raise RuntimeError('Window diagnostic resource/evidence gate failed')


if __name__=='__main__':main()
