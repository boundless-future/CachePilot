"""Fixed anonymous-structure pilot then identical-source formal, no diagnostic probes."""
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
    from audit_shared_structure_matrix import audit, audit_one
    if Path(ablation.__file__).resolve() != Path(__file__).with_name('run_protection_ablation.py').resolve():
        raise RuntimeError('Wrong measurement helper')
    sources = ablation.source_sha()
    prerequisite = {}
    provenance = validate_inputs(args.input/'built', args.input/'source', args.input/'catalogue.json', ablation.ROOT/'models/Qwen3-4B')
    if args.stage == 'pilot':
        if args.admission is None:
            parser.error('Pilot needs completed historical shared formal')
        from audit_shared_strategy_matrix import audit as prior_audit
        prior = read(args.admission/'plan.json')
        check_admission_sources(prior['signature']['source_sha256'], sources)
        if prior['stage'] != 'formal' or not prior_audit(args.admission)['matrix_complete']:
            raise ValueError('Historical shared formal prerequisite failed')
        prerequisite = dict(historical_formal=str(args.admission))
        trace = read(args.input/'built/trace.json')
    else:
        if args.pilot is None:
            parser.error('Formal needs --pilot')
        pilot = read(args.pilot/'plan.json')
        if (pilot['stage'] != 'pilot' or pilot['signature']['source_sha256'] != sources
                or not audit(args.pilot)['matrix_complete']):
            raise ValueError('Complete same-source pilot required')
        trace = read(args.pilot/'trace.json')
        prerequisite = dict(pilot=str(args.pilot), signature=pilot['signature'])
    if trace != read(args.input/'built/trace.json') or digest(trace) != VALIDATION_SHA:
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
                   cases=CASES,gate='shared-prefix-structure-v1',hotpath_diagnostics=False)
    env.write_json(out/'plan.json',dict(stage=args.stage,repeats=repeats,cases=CASES,signature=signature,
        orders=[order(i) for i in range(repeats)],prerequisite=prerequisite,
        scope='Historical anonymous six-session structure; open arrival, not live Agent', stop_starting_at=args.stop_starting_at))
    shutil.copytree(args.input, out/'input')
    env.write_json(out/'provenance-audit.json', provenance)
    env.write_json(out/'trace.json',trace)
    env.write_json(out/'tokenizer-audit.json',dict(passed=True,lengths=lengths,total=sum(lengths)))
    with tarfile.open(out/'frozen-source.tar.gz','w:gz') as archive:
        for name in sources:archive.add(ablation.ROOT/name,arcname=name)
    current=None
    class Service(env.Service):
        def start(self,health,timeout=900):
            os.environ['CACHEPILOT_OWNED_EVENT_LOG']='0'
            if self.log.name=='lmcache.log':
                self.command=[sys.executable,str(ablation.ROOT/'scripts/generation_service_observer.py'),
                    'server','--host','127.0.0.1','--port','5555','--http-host','127.0.0.1','--http-port','8080',
                    '--l1-size-gb','16','--l1-init-size-gb','16','--eviction-policy','LRU']
            else:
                i=self.command.index('--kv-transfer-config')+1
                config=configure(json.loads(self.command[i]),current)
                self.command[i]=json.dumps(config)
                env.write_json(self.log.parent/'config.json',config)
                env.write_json(self.log.parent/'launch-command.json',self.command)
                os.environ['CACHEPILOT_GENERATION_SUMMARY_PATH']=str(self.log.parent/'generation-summary.json')
            return super().start(health,timeout)
    ablation.Service=Service
    original=ablation.cache_drained
    ablation.cache_drained=lambda state:original(state) and strict_idle(state)
    for repeat in range(repeats):
        for name in order(repeat):
            if datetime.now(timezone.utc) >= cutoff:
                env.write_json(out/'stopped.json', dict(reason='deadline', next_cell=f'{name}-r{repeat}'))
                env.write_json(out/'independent-summary.json', audit(out))
                return
            if ablation.source_sha()!=sources:raise RuntimeError('Frozen matrix source changed')
            ablation.ports_free();current=CASES[name];cell=out/f'{name}-r{repeat}'
            print('START '+cell.name,flush=True)
            try:
                ablation.run_cell(cell,current['mode'],repeat,trace,str(ablation.ROOT/'models/Qwen3-4B'),KV_BYTES)
                row=audit_one(cell,name,repeat,signature,trace)
                env.write_json(cell/'matrix-validation.json',row)
                if not row['passed']:raise RuntimeError('Matrix cell audit failed')
            except BaseException as exc:
                env.write_json(out/'stopped.json',dict(cell=cell.name,error=repr(exc)))
                env.write_json(out/'independent-summary.json',audit(out));raise
            env.write_json(out/'independent-summary.json',audit(out))
            print('DONE '+cell.name,flush=True)


if __name__=='__main__':main()
