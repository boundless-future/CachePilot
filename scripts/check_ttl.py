"""Observe actual LMCache session TTL after vLLM stops; up to 700 seconds."""
import argparse
import json
import signal
import socket
import time
from pathlib import Path
from validate_environment import Service, ROOT, API, CACHE, request, get_text, write_json


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    for port in (8000,8080,5555):
        with socket.socket() as sock:
            if sock.connect_ex(('127.0.0.1',port))==0:
                raise RuntimeError(f'Port {port} occupied')
    out=args.output;out.mkdir(parents=True,exist_ok=False)
    def stop_signal(*_): raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,stop_signal)
    cache=Service(['bash',str(ROOT/'scripts/lmcache-server.sh')],out/'lmcache.log')
    engine=Service(['bash',str(ROOT/'scripts/serve.sh'),'fifo'],out/'vllm.log')
    try:
        cache.start(CACHE+'/status');engine.start(API+'/health')
        request(out,'cold');request(out,'hot')
        request(out,'pressure','Different prefix '+('tool context. '*600))
        engine.stop()
        start=time.monotonic();initial=None
        while time.monotonic()-start<700:
            state=json.loads(get_text(CACHE+'/status'));elapsed=time.monotonic()-start
            if initial is None:initial=state['active_sessions']
            row=dict(elapsed_seconds=elapsed,active_sessions=state['active_sessions'],
                registered_gpu_ids=state['registered_gpu_ids'],l1=state['storage_manager']['l1_manager'])
            with (out/'observations.jsonl').open('a') as f:f.write(json.dumps(row)+'\n')
            print(json.dumps({k:row[k] for k in ['elapsed_seconds','active_sessions','registered_gpu_ids']}),flush=True)
            if not state['active_sessions'] and elapsed>5:
                write_json(out/'result.json',dict(initial_sessions=initial,sessions_cleared=True,
                    elapsed_seconds=elapsed,last_observation=row));break
            time.sleep(15)
        else:
            write_json(out/'result.json',dict(initial_sessions=initial,sessions_cleared=False,last_observation=row))
            raise RuntimeError('TTL deadline exceeded')
    finally:
        engine.stop();cache.stop()


if __name__=='__main__':main()
