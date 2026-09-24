"""Fetch pinned anonymous WEKA traces; reconstruct scaled token blocks, not real text."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import argparse

import requests
from transformers import AutoTokenizer

COMMIT='94f6046dfd9c0c8bc0cdb24a5aa6579bac8669bf'
BASE=f'https://raw.githubusercontent.com/callanjfox/kv-cache-tester/{COMMIT}'


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',required=True,type=Path);p.add_argument('--model',required=True)
    a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
    tok=AutoTokenizer.from_pretrained(a.model)
    def fetch(i):
        name=f'trace_{i:04d}.json';r=requests.get(BASE+'/traces/'+name,timeout=45);r.raise_for_status()
        (a.output/name).write_bytes(r.content)
        return name,r.json(),hashlib.sha256(r.content).hexdigest()
    with ThreadPoolExecutor(max_workers=4) as pool: source=list(pool.map(fetch,range(1,21)))
    manifest=[];events=[];selected=[]
    # Common harmless word tokens. IDs are reconstructed deterministically per source-local block hash.
    vocab=[tok.encode(' '+w,add_special_tokens=False)[0] for w in ['red','blue','green','cat','tree','code','cache','test','data','tool','word','file','model','task','state','step']]
    for name,t,sha in source:
        all_rows=t['requests'];rs=[r for r in all_rows if 'in' in r]
        nested=any('requests' in r for r in all_rows)
        peak=max(r['in'] for r in rs);fits=peak+48<=8192 and not nested
        scaled_peak=peak//8
        eligible=not nested and t['block_size']==64 and scaled_peak+48<=8192 and 3<=len(rs)<=32 and all(r.get('hash_ids') for r in rs)
        manifest.append(dict(file=name,sha256=sha,requests=len(rs),peak_original_tokens=peak,
            fits_8k_unscaled=fits,scaled_peak=scaled_peak,eligible_scaled=eligible,nested_subagents_excluded=nested))
        if not eligible or len(selected)>=4:continue
        sid=t['id'];selected.append(name)
        first=rs[0]['t']
        for turn,r in enumerate(rs):
            ids=[]
            for h in r['hash_ids']:
                digest=hashlib.sha256(f'{sid}:{h}'.encode()).digest()
                ids.extend(vocab[x%len(vocab)] for x in digest[:8])
            length=r['in']//8
            # Tail is incomplete source block; avoid claiming it is reused.
            digest=hashlib.sha256(f'{sid}:tail:{turn}'.encode()).digest()
            ids=(ids+[vocab[x%len(vocab)] for x in digest])[:length]
            assert len(ids)==length
            encoded=json.dumps(ids,separators=(',',':')).encode()
            events.append(dict(event_id=len(events),session_id=sid,turn=turn,
                arrival_ms=(r['t']-first)*1000/50,think_ms=max(0,r.get('think_time',0))*1000/50,
                prompt=ids,prompt_sha256=hashlib.sha256(encoded).hexdigest(),prompt_tokens=len(ids),
                max_tokens=48,seed=42,temperature=0,source_input_tokens=r['in'],source_output_tokens=r['out']))
    assert selected,'No complete scaled session fits'
    trace=dict(schema_version=2,trace_id='weka-scaled-first20',append_only=False,events=events,
        semantics='Anonymous hash-block reconstruction, token lengths /8, times /50, fixed output 48; no original text or tool execution.')
    (a.output/'real-trace.json').write_text(json.dumps(trace,indent=2)+'\n')
    (a.output/'manifest.json').write_text(json.dumps(dict(repository=BASE,commit=COMMIT,
        sampled_rule='first 20 numbered traces at fixed commit',selected=selected,sources=manifest,
        transform=trace['semantics']),indent=2)+'\n')
    print(json.dumps(dict(selected=selected,events=len(events),unscaled_fits=sum(m['fits_8k_unscaled'] for m in manifest))))


if __name__=='__main__': main()
