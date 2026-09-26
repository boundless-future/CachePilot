"""Require actual, covered KV comparisons; missing references are not passes."""
import argparse
import collections
import json
from pathlib import Path


def summarize_probe(rows):
    counts = collections.Counter()
    missing, unequal = [], []
    for row in rows:
        counts[row['phase'] + '_chunks'] += 1
        if row['phase'] != 'after_retrieve':
            continue
        counts['retrieved_layers'] += len(row['layers'])
        if not row['layers']:
            missing.append(dict(request_id=row['request_id'], end=row['end'], reason='empty layers'))
        for layer in row['layers']:
            detail = dict(request_id=row['request_id'], start=row['start'], end=row['end'],
                          layer=layer['layer'], token_prefix_sha256=row['token_prefix_sha256'])
            if not layer.get('reference_present') or 'bitwise_equal' not in layer:
                missing.append(detail)
            elif layer['bitwise_equal']:
                counts['equal_layers'] += 1
            else:
                unequal.append(dict(**detail, sha256=layer['sha256'],
                                    max_abs_difference=layer.get('max_abs_difference')))
    return dict(counts=dict(counts), missing_references=missing, unequal_layers=unequal,
                covered_and_equal=bool(counts['retrieved_layers']) and not missing and not unequal)


def compare_outputs(reference, candidate):
    ref = {r['event_id']: r for r in reference}
    other = {r['event_id']: r for r in candidate}
    if len(ref) != len(reference) or len(other) != len(candidate) or ref.keys() != other.keys():
        raise ValueError('Outputs must have unique matching event IDs')
    differences = []
    for eid, row in ref.items():
        a = row['response']['choices'][0]
        b = other[eid]['response']['choices'][0]
        if a['text'] == b['text']:
            continue
        al, bl = a.get('logprobs'), b.get('logprobs')
        detail = dict(event_id=eid, turn=row['turn'])
        if al and bl:
            first = next((i for i, (at, bt) in enumerate(zip(al['tokens'], bl['tokens'])) if at != bt), None)
            detail['first_differing_token_index'] = first
            if first is not None:
                detail.update(reference_top5=al['top_logprobs'][first], candidate_top5=bl['top_logprobs'][first])
        differences.append(detail)
    return dict(requests=len(ref), mismatches=len(differences), differences=differences)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('directory', type=Path)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    rows = [json.loads(line) for path in sorted((a.directory/'probe/probe').glob('kv-*.jsonl'))
            for line in path.read_text().splitlines()]
    output = dict(kv=summarize_probe(rows), outputs={})
    modes = {mode: json.loads((a.directory/mode/'responses.json').read_text())
             for mode in ['baseline','immediate','probe']}
    for ref, candidate in [('baseline','immediate'), ('baseline','probe'), ('immediate','probe')]:
        output['outputs'][ref+'__'+candidate] = compare_outputs(modes[ref], modes[candidate])
    a.output.write_text(json.dumps(output,indent=2)+'\n')
    print(json.dumps(output,indent=2))
    if not output['kv']['covered_and_equal']:
        raise SystemExit('KV equality not established; inspect missing references or unequal layers')


if __name__ == '__main__':
    main()
