"""Separate, instrumented capacity replay. Never aggregate as performance data."""
import argparse
import functools
import json
import os
from pathlib import Path
import sys


def owner_snapshot(service):
    # Caller holds service.gate; never query/consume a controller result here.
    return [dict(request_id=o.request_id, abandoned=o.abandoned,
        consumed=o.consumed, claimed=o.claimed, has_handle=o.handle is not None,
        hit_chunks=o.hit_chunks, reads=len(o.reads), writes=len(o.writes),
        error=o.error) for o in service.jobs.values()]


def observe_method(cls, name, service, identify):
    original = getattr(cls, name)

    @functools.wraps(original)
    def observed(self, *args, **kwargs):
        request_id = identify(*args, **kwargs)
        with service.gate:
            service.record('diagnostic_' + name + '_enter', request_id)
            try:
                result = original(self, *args, **kwargs)
            except BaseException as exc:
                service.record('diagnostic_' + name + '_error', request_id,
                               error=repr(exc))
                raise
            service.record('diagnostic_' + name + '_return', request_id,
                           result=result if type(result) in (int, bool, type(None)) else repr(result))
            return result
    setattr(cls, name, observed)


def serve():
    from owned_service import install_service
    from lmcache.v1.multiprocess.modules.lookup import LookupModule
    from lmcache.cli.main import main
    service = install_service(os.environ['CACHEPILOT_OWNED_SERVICE_DIR'])
    for name in ('query_prefetch_status', 'end_session'):
        observe_method(LookupModule, name, service, lambda request_id: request_id)
    observe_method(LookupModule, 'lookup', service, lambda key, tp_size: key.request_id)
    original = LookupModule.report_status

    def report(self):
        with service.gate:
            return original(self) | {'diagnostic_owners': owner_snapshot(service)}
    LookupModule.report_status = report
    sys.argv[0] = 'lmcache'
    return main()


def run():
    import run_protection_ablation as ablation
    from validate_environment import Service, write_json
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--trace', type=Path, required=True)
    parser.add_argument('--observe-scheduler', action='store_true')
    parser.add_argument('--resume-pressure', action='store_true',
                        help='After the unchanged idle gate, add three diagnostic requests.')
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    trace = json.loads(args.trace.read_text())
    ablation.validate_trace(trace)
    ablation.ports_free()
    write_json(output / 'diagnostic-plan.json', dict(
        purpose='Observe 2.5 GiB zero-budget owner lifecycle; not performance data',
        source_trace=str(args.trace), kv_cache_bytes=2684354560,
        modifications=['server lifecycle events', 'read-only owner snapshots'],
        observe_scheduler=args.observe_scheduler,
        resume_pressure=args.resume_pressure,
        drain_timeout_seconds=45))

    class DiagnosticService(Service):
        def start(self, health, timeout=900):
            os.environ['CACHEPILOT_OWNED_EVENT_LOG'] = '1'
            if self.log.name == 'lmcache.log':
                self.command = [sys.executable, str(Path(__file__).resolve()),
                    'server', '--host', '127.0.0.1', '--port', '5555',
                    '--http-host', '127.0.0.1', '--http-port', '8080',
                    '--l1-size-gb', '16', '--l1-init-size-gb', '16',
                    '--eviction-policy', 'LRU', '--enable-extra-logging']
            elif args.observe_scheduler:
                index = self.command.index('--kv-transfer-config') + 1
                config = json.loads(self.command[index])
                config['kv_connector'] = 'CapacityOwnerConnector'
                config['kv_connector_module_path'] = 'scripts.capacity_owner_connector'
                self.command[index] = json.dumps(config)
                write_json(self.log.parent / 'config.json', config)
            return super().start(health, timeout)

    original_json = ablation.json_url

    def snapshot(url):
        data = original_json(url)
        if url.endswith('/status'):
            import time
            with (output / 'status-timeline.jsonl').open('a') as stream:
                stream.write(json.dumps(dict(unix_time=time.time(), status=data)) + '\n')
        return data
    ablation.Service = DiagnosticService
    ablation.json_url = snapshot
    if args.resume_pressure:
        idle_gate = ablation.wait_drained

        def resume():
            import requests
            import time
            from transformers import AutoTokenizer
            before = idle_gate()
            write_json(output / 'before-resume.json', before)
            tokenizer = AutoTokenizer.from_pretrained(str(ablation.ROOT / 'models/Qwen3-4B'))
            # Fixed, sequential pressure intervention, not a retry of the
            # benchmark. Original 68-request metrics are already captured.
            tail = ablation.generate(tokenizer, 3, 1, 7168,
                period_ms=1800, think_ms=100, session_gap_ms=20)
            write_json(output / 'resume-trace.json', tail)
            rows = []
            for event in tail['events']:
                payload = dict(model=str(ablation.ROOT / 'models/Qwen3-4B'),
                    prompt=event['prompt'], max_tokens=8, seed=42,
                    temperature=0, ignore_eos=True)
                response = requests.post(ablation.API + '/v1/completions',
                                         json=payload, timeout=180)
                rows.append(dict(unix_time=time.time(), http_status=response.status_code,
                                 response=response.json()))
                write_json(output / 'resume-responses.json', rows)
                response.raise_for_status()
                if rows[-1]['response']['usage']['completion_tokens'] != 8:
                    raise RuntimeError('Diagnostic tail token contract failed')
            after = idle_gate()
            write_json(output / 'after-resume.json', after)
            return after
        ablation.wait_drained = resume
    ablation.run_cell(output / 'zero-r0', 'zero', 0, trace,
                     str(ablation.ROOT / 'models/Qwen3-4B'), 2684354560)


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'server':
        sys.exit(serve())
    run()
