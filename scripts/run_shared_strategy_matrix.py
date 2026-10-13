"""Fixed historical-text pilot then identical-source formal, no diagnostic probes."""
import argparse
import json
import os
from pathlib import Path
import sys
import tarfile
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(1, str(Path(__file__).resolve().parents[1]))
from run_generation_strategy_pilot import strategy_config, KV_BYTES, strict_idle, read, digest
from audit_generation_strategy_validation import VALIDATION_SHA

CASES = {
    'default-zero': dict(kind='lazy', horizon=2.5, drain=64, mode='zero'),
    'selected-zero': dict(kind='lazy', horizon=5.0, drain=1, mode='zero'),
    'shared-one': dict(kind='lazy', horizon=5.0, drain=1, mode='one'),
    'shared-two': dict(kind='lazy', horizon=5.0, drain=1, mode='two'),
}
ADDITIONS = {'scripts/run_shared_strategy_matrix.py', 'scripts/audit_shared_strategy_matrix.py'}
REVIEWED_CHANGES = {'scripts/audit_shared_prefix_trace.py', 'scripts/run_generation_protection_smoke.py'}


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
    changed = {p for p,sha in previous.items() if current.get(p) != sha}
    if not changed <= REVIEWED_CHANGES or set(current)-set(previous) != ADDITIONS:
        raise ValueError('Only reviewed harness changes and two matrix additions allowed')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--stage', choices=('pilot', 'formal'), required=True)
    parser.add_argument('--admission', type=Path)
    parser.add_argument('--pilot', type=Path)
    parser.add_argument('--trace', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    import run_protection_ablation as ablation
    import validate_environment as env
    from audit_shared_strategy_matrix import audit, audit_one
    if Path(ablation.__file__).resolve() != Path(__file__).with_name('run_protection_ablation.py').resolve():
        raise RuntimeError('Wrong measurement helper')
    sources = ablation.source_sha()
    prerequisite = {}
    if args.stage == 'pilot':
        if args.admission is None or args.trace is None:
            parser.error('Pilot needs --admission and --trace')
        from scripts.audit_shared_prefix_trace import audit as shared_admission
        from scripts.audit_generation_protection_smoke import audit_cell
        from scripts.audit_generation_shared import audit as shared_fault
        root = args.admission.parent
        check_admission_sources(read(args.admission/'plan.json')['signature']['source_sha256'], sources)
        prerequisite['shared_restore'] = shared_admission(args.admission)['passed']
        for mode in ('zero','one','two'):
            prerequisite['normal-'+mode] = audit_cell(root/'shared-prefix-normal-20261013a'/mode)['passed']
        for name in ('cancel','preempt'):
            prerequisite[name] = shared_fault(root/f'shared-prefix-{name}-20261013a'/'two', preemption=name=='preempt')['passed']
        for name in ('failed','reuse'):
            prerequisite[name] = audit_cell(root/f'shared-prefix-{name}-20261013a'/'two')['passed']
        if not all(prerequisite.values()):
            raise ValueError('Independent GPU admission incomplete')
        trace = read(args.trace)
    else:
        if args.pilot is None:
            parser.error('Formal needs --pilot')
        pilot = read(args.pilot/'plan.json')
        if (pilot['stage'] != 'pilot' or pilot['signature']['source_sha256'] != sources
                or not audit(args.pilot)['matrix_complete']):
            raise ValueError('Complete same-source pilot required')
        trace = read(args.pilot/'trace.json')
        prerequisite = dict(pilot=str(args.pilot), signature=pilot['signature'])
    if digest(trace) != VALIDATION_SHA:
        raise ValueError('Historical input changed')
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(ablation.ROOT/'models/Qwen3-4B')
    lengths = [len(tokenizer.encode(e['prompt'],add_special_tokens=False)) for e in trace['events']]
    if lengths != [e['prompt_tokens'] for e in trace['events']]:
        raise ValueError('Actual tokenizer lengths differ')
    ablation.ports_free()
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    repeats=1 if args.stage=='pilot' else 3
    signature=dict(source_sha256=sources,trace_sha256=VALIDATION_SHA,kv_cache_bytes=KV_BYTES,
                   cases=CASES,gate='shared-prefix-historical-v1',hotpath_diagnostics=False)
    env.write_json(out/'plan.json',dict(stage=args.stage,repeats=repeats,cases=CASES,signature=signature,
        orders=[order(i) for i in range(repeats)],prerequisite=prerequisite,
        scope='Historical synthetic input; diagnostic and pilot excluded from formal'))
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
