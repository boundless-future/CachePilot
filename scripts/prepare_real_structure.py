"""Reconstruct all structurally eligible held-out sessions; CPU only.

Source-local hashes represent anonymous blocks, not text or generated output.
No padding, synthetic pressure, session copies or performance selection.
"""
import argparse
import hashlib
import json
from pathlib import Path

try:
    from .catalogue_real_traces import inspect_trace
except ImportError:
    from catalogue_real_traces import inspect_trace

WORDS = ['red', 'blue', 'green', 'cat', 'tree', 'code', 'cache', 'test',
         'data', 'tool', 'word', 'file', 'model', 'task', 'state', 'step']
ELIGIBLE = ['trace_0021.json', 'trace_0022.json', 'trace_0026.json',
            'trace_0057.json', 'trace_0058.json', 'trace_0086.json']


def build(source_dir, catalogue, vocabulary):
    if catalogue['eligible'] != ELIGIBLE or len(vocabulary) != 16 or len(set(vocabulary)) != 16:
        raise ValueError('Frozen complete selection and 16 distinct single-token words required')
    manifest = {r['file']: r for r in catalogue['sources']}
    events, session_ids = [], set()
    for name in ELIGIBLE:
        raw = (source_dir / name).read_bytes()
        structural = inspect_trace(name, raw)
        if structural != manifest[name] or not structural['eligible']:
            raise ValueError('Source bytes or eligibility changed: ' + name)
        trace = json.loads(raw)
        sid = trace['id']
        if sid in session_ids:
            raise ValueError('Duplicate source session')
        session_ids.add(sid)
        rows = [r for r in trace['requests'] if 'in' in r]
        if any(b['t'] < a['t'] for a, b in zip(rows, rows[1:])):
            raise ValueError('Source turns are not monotonic')
        first = rows[0]['t']
        for turn, row in enumerate(rows):
            ids = []
            for hash_id in row['hash_ids']:
                digest = hashlib.sha256(f'{sid}:{hash_id}'.encode()).digest()
                ids.extend(vocabulary[x % 16] for x in digest[:8])
            length = row['in'] // 8
            # Source incomplete tails do not acquire cross-turn identity.
            digest = hashlib.sha256(f'{sid}:tail:{turn}'.encode()).digest()
            ids = (ids + [vocabulary[x % 16] for x in digest])[:length]
            if len(ids) != length or not 0 < length <= 8192 - 48:
                raise ValueError('Source hashes cannot reconstruct scaled length')
            sha = hashlib.sha256(json.dumps(ids, separators=(',', ':')).encode()).hexdigest()
            events.append(dict(session_id=sid, turn=turn, source_file=name,
                arrival_ms=(row['t'] - first) * 1000 / 10,
                think_ms=max(0, row.get('think_time', 0)) * 1000 / 10,
                prompt=ids, prompt_sha256=sha, prompt_tokens=length,
                max_tokens=48, seed=42, temperature=0,
                source_input_tokens=row['in'], source_output_tokens=row['out']))
    events.sort(key=lambda r: (r['arrival_ms'], r['source_file'], r['turn']))
    for index, event in enumerate(events):
        event['event_id'] = index
    if len(events) != 60 or len(session_ids) != 6:
        raise ValueError('Frozen source request/session counts changed')
    return dict(schema_version=2, trace_id='weka-heldout21-100-six-session',
        append_only=False, events=events,
        semantics='Anonymous source-local hash reconstruction; tokens /8, per-session times /10, first arrivals aligned; fixed output48; no source text/tools/output feedback.')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--source-dir', type=Path, required=True)
    p.add_argument('--catalogue', type=Path, required=True)
    p.add_argument('--model', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    from transformers import AutoTokenizer
    tok = AutoTokenizer.from_pretrained(a.model, local_files_only=True)
    encodings = [tok.encode(' ' + word, add_special_tokens=False) for word in WORDS]
    if any(len(ids) != 1 for ids in encodings):
        raise ValueError('Vocabulary words must each encode to exactly one token')
    vocabulary = [ids[0] for ids in encodings]
    report = json.loads(a.catalogue.read_text(encoding='utf-8'))
    trace = build(a.source_dir, report, vocabulary)
    a.output.mkdir(parents=True, exist_ok=False)
    (a.output / 'trace.json').write_text(json.dumps(trace, indent=2) + '\n', encoding='utf-8')
    files = ['tokenizer.json', 'tokenizer_config.json', 'special_tokens_map.json', 'vocab.json', 'merges.txt']
    signature = {name: hashlib.sha256((a.model / name).read_bytes()).hexdigest()
                 for name in files if (a.model / name).is_file()}
    if 'tokenizer.json' not in signature:
        raise ValueError('Tokenizer byte signature required')
    (a.output / 'manifest.json').write_text(json.dumps(dict(cpu_only=True,
        model=str(a.model), tokenizer_sha256=signature, vocabulary=vocabulary,
        catalogue_sha256=hashlib.sha256(a.catalogue.read_bytes()).hexdigest(),
        builder_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        trace_sha256=hashlib.sha256(json.dumps(trace, sort_keys=True).encode()).hexdigest(),
        selected=ELIGIBLE, semantics=trace['semantics']), indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
