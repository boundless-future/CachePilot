"""CPU-only structural eligibility audit; never selects by measured performance."""
import argparse
import hashlib
import json
from pathlib import Path

COMMIT = '94f6046dfd9c0c8bc0cdb24a5aa6579bac8669bf'


def inspect_trace(name, raw):
    trace = json.loads(raw)
    rows = [r for r in trace['requests'] if 'in' in r]
    peak = max((r['in'] for r in rows), default=0)
    nested = any('requests' in r for r in trace['requests'])
    reasons = []
    if nested:
        reasons.append('nested_subagents')
    if trace['block_size'] != 64:
        reasons.append('block_size')
    if not 3 <= len(rows) <= 32:
        reasons.append('request_count')
    if peak // 8 + 48 > 8192:
        reasons.append('context_bound')
    if not rows or not all(r.get('hash_ids') for r in rows):
        reasons.append('missing_hash_blocks')
    times = [r['t'] for r in rows]
    return dict(file=name, sha256=hashlib.sha256(raw).hexdigest(),
        requests=len(rows), peak_original_tokens=peak, scaled_peak=peak // 8,
        original_duration_seconds=max(times) - min(times) if times else 0,
        nested_subagents_excluded=nested, block_size=trace['block_size'],
        eligible=not reasons, exclusion_reasons=reasons)


def catalogue(directory, first=21, last=100):
    if first < 21 or last < first:
        raise ValueError('Held-out catalogue starts after the original first20')
    sources = []
    for number in range(first, last + 1):
        name = f'trace_{number:04d}.json'
        sources.append(inspect_trace(name, (directory / name).read_bytes()))
    return dict(commit=COMMIT,
        repository='https://github.com/callanjfox/kv-cache-tester',
        rule=f'Numbered {first}-{last}; original /8 eligibility; all eligible sessions; no performance selection',
        cpu_only=True, sources=sources,
        eligible=[r['file'] for r in sources if r['eligible']])


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--source-dir', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    report = catalogue(a.source_dir)
    a.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(dict(eligible=report['eligible'], sources=len(report['sources']))))
