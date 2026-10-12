"""CPU audit of archived anonymous source reconstruction, never a GPU gate."""
import argparse
import hashlib
import itertools
import json
from pathlib import Path

try:
    from .generate_trace import validate_trace
except ImportError:
    from generate_trace import validate_trace


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prefix(a, b):
    return next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))


def audit(directory, source_dir, catalogue_path):
    trace = json.loads((directory / 'trace.json').read_text())
    manifest = json.loads((directory / 'manifest.json').read_text())
    catalogue = json.loads(catalogue_path.read_text())
    validate_trace(trace)
    assert sha(catalogue_path) == manifest['catalogue_sha256']
    assert hashlib.sha256(json.dumps(trace, sort_keys=True).encode()).hexdigest() == manifest['trace_sha256']
    assert manifest['selected'] == catalogue['eligible']
    vocab = manifest['vocabulary']
    assert len(vocab) == len(set(vocab)) == 16
    rows = {r['file']: r for r in catalogue['sources']}
    events = trace['events']
    sessions, block_ids, transitions = {}, {}, []
    for name in manifest['selected']:
        source_path = source_dir / name
        assert sha(source_path) == rows[name]['sha256']
        source = json.loads(source_path.read_text())
        source_rows = [r for r in source['requests'] if 'in' in r]
        selected = [e for e in events if e['source_file'] == name]
        assert len(source_rows) == len(selected)
        sessions[source['id']] = selected
        previous = None
        for turn, (raw, event) in enumerate(zip(source_rows, selected)):
            assert event['session_id'] == source['id'] and event['turn'] == turn
            assert event['arrival_ms'] == (raw['t'] - source_rows[0]['t']) * 100
            assert abs(event['think_ms'] - max(0, raw.get('think_time', 0)) * 100) < 1e-7
            assert event['prompt_tokens'] == len(event['prompt']) == raw['in'] // 8
            assert event['max_tokens'] == 48 and len(event['prompt']) + 48 <= 8192
            assert event['temperature'] == 0 and event['seed'] == 42
            assert set(event['prompt']) <= set(vocab)
            assert len(raw['hash_ids']) * 64 <= raw['in'] < (len(raw['hash_ids']) + 1) * 64
            for index, identity in enumerate(raw['hash_ids']):
                tokens = tuple(event['prompt'][index * 8:index * 8 + 8])
                key = (source['id'], identity)
                assert len(tokens) == 8
                assert block_ids.setdefault(key, tokens) == tokens
            if previous:
                old_raw, old_event = previous
                source_prefix = prefix(old_raw['hash_ids'], raw['hash_ids']) * 8
                actual_prefix = prefix(old_event['prompt'], event['prompt'])
                assert actual_prefix >= source_prefix
                # Hash identity ends at the incomplete tail; incidental token
                # matches there are measured, not treated as source identity.
                transitions.append(dict(session_id=source['id'], turn=turn,
                    source_complete_prefix_tokens=source_prefix,
                    actual_prefix_tokens=actual_prefix,
                    reusable_full_chunks=actual_prefix // 256))
            previous = raw, event
    assert len(events) == 60 and len(sessions) == 6
    assert [e['event_id'] for e in events] == list(range(60))
    assert events == sorted(events, key=lambda e: (e['arrival_ms'], e['source_file'], e['turn']))
    assert len({e['prompt_sha256'] for e in events}) == len(events)
    assert len(set(block_ids.values())) == len(block_ids), 'Different source blocks collided'
    cross = [prefix(a['prompt'], b['prompt']) for a, b in itertools.combinations(events, 2)
             if a['session_id'] != b['session_id']]
    assert max(cross) < 16, 'Accidental cross-session physical cache block'
    return dict(passed=True, cpu_only=True, trace_sha256=manifest['trace_sha256'],
        requests=len(events), sessions=len(sessions), prompt_tokens=sum(e['prompt_tokens'] for e in events),
        output_token_budget=sum(e['max_tokens'] for e in events),
        last_scheduled_arrival_ms=max(e['arrival_ms'] for e in events),
        distinct_source_blocks=len(block_ids), cross_session_max_prefix_tokens=max(cross),
        transitions=transitions,
        limitations='Open arrival replay; no generated-output feedback or live tool execution. Source global arrival relation unknown. CPU prefix opportunity is not measured GPU cache hit or pressure.')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--directory', type=Path, required=True)
    p.add_argument('--source-dir', type=Path, required=True)
    p.add_argument('--catalogue', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    result = audit(a.directory, a.source_dir, a.catalogue)
    a.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
