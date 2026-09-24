"""Stream fixed traces; open-loop keeps arrivals, closed-loop uses per-session think time."""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import time

import aiohttp
from generate_trace import validate_trace


async def consume_sse(content, on_chunk):
    # aiohttp line iteration preserves SSE lines across network packet boundaries.
    done=False
    async for raw in content:
        line=raw.decode('utf-8').strip()
        if not line.startswith('data:'): continue
        data=line[5:].strip()
        if data=='[DONE]':
            done=True
            break
        on_chunk(json.loads(data))
    if not done: raise RuntimeError('truncated SSE stream (missing DONE)')


async def replay(trace, base_url, model, mode='open', limit=64):
    validate_trace(trace)
    start=time.perf_counter()
    completions={}
    for e in trace['events']: completions[(e['session_id'],e['turn'])]=asyncio.Event()
    finished={}
    sem=asyncio.Semaphore(limit)
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=180),
                                    connector=aiohttp.TCPConnector(limit=0)) as client:
        async def send(e):
            scheduled=start+e['arrival_ms']/1000
            predecessor=(e['session_id'],e['turn']-1)
            if mode=='closed' and e['turn']:
                await completions[predecessor].wait()
                scheduled=finished[predecessor]+e['think_ms']/1000
            await asyncio.sleep(max(0,scheduled-time.perf_counter()))
            # Open-loop intentionally has no dependency gate; offered load is identical.
            # It is a serving workload, not a live causally executing Agent.
            row={k:e[k] for k in ['event_id','session_id','turn','arrival_ms','prompt_sha256','prompt_tokens','max_tokens']}
            row.update(scheduled_ms=(scheduled-start)*1000,error=None,usage=None)
            first=None; text=[]; chunks=[]; usage=None; finish_reason=None
            def receive(chunk):
                nonlocal first,usage,finish_reason
                if chunk.get('error'): raise RuntimeError(str(chunk['error']))
                if chunk.get('usage'): usage=chunk['usage']
                for choice in chunk.get('choices',[]):
                    piece=choice.get('text','')
                    if piece:
                        now=time.perf_counter()
                        if first is None: first=now
                        chunks.append((now-start)*1000)
                        text.append(piece)
                    if choice.get('finish_reason'): finish_reason=choice['finish_reason']
            async with sem:
                sent=time.perf_counter()
                row['sent_ms']=(sent-start)*1000
                payload=dict(model=model,prompt=e['prompt'],max_tokens=e['max_tokens'],temperature=0,
                    seed=e['seed'],ignore_eos=True,stream=True,stream_options={'include_usage':True})
                try:
                    async with client.post(base_url+'/v1/completions',json=payload) as response:
                        row['http_status']=response.status
                        response.raise_for_status()
                        await consume_sse(response.content,receive)
                    if not usage or usage['completion_tokens']!=e['max_tokens']:
                        raise RuntimeError(f'output budget mismatch: {usage}')
                    if usage['prompt_tokens']!=e['prompt_tokens']:
                        raise RuntimeError(f'input token count mismatch: {usage}')
                    if first is None: raise RuntimeError('no nonempty text chunk')
                except Exception as exc: row['error']=repr(exc)
                end=time.perf_counter()
            key=(e['session_id'],e['turn'])
            finished[key]=end
            completions[key].set()
            row.update(finished_ms=(end-start)*1000,client_queue_ms=(sent-scheduled)*1000,
                ttft_ms=None if first is None else (first-sent)*1000,
                ready_to_first_ms=None if first is None else (first-scheduled)*1000,
                e2e_ms=(end-sent)*1000,ready_to_end_ms=(end-scheduled)*1000,
                output_text=''.join(text),usage=usage,finish_reason=finish_reason,
                chunk_arrivals_ms=chunks)
            row['output_sha256']=hashlib.sha256(row['output_text'].encode()).hexdigest()
            return row
        rows=await asyncio.gather(*(send(e) for e in trace['events']))
    return dict(trace_id=trace['trace_id'],mode=mode,client_inflight_limit=limit,results=rows)


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--trace',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--model',required=True)
    p.add_argument('--base-url',default='http://127.0.0.1:8000')
    p.add_argument('--mode',choices=['open','closed'],default='open')
    a=p.parse_args()
    data=asyncio.run(replay(json.loads(a.trace.read_text(encoding='utf-8')),a.base_url,a.model,a.mode))
    data['trace_sha256']=hashlib.sha256(a.trace.read_bytes()).hexdigest()
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(data,indent=2)+'\n')
    if any(r['error'] for r in data['results']): raise SystemExit(1)


if __name__=='__main__': main()
