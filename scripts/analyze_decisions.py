"""Summarize an EVICTION_AWARE decision ledger without exposing prompts."""
import argparse
import collections
import json
from pathlib import Path
import statistics


def main():
    p = argparse.ArgumentParser()
    p.add_argument('directory', type=Path)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    rows = []
    for path in sorted(args.directory.glob('scheduler-*.jsonl')):
        rows.extend(json.loads(line) for line in path.read_text().splitlines())
    counts = collections.Counter(row['event'] for row in rows)
    drops = [row for row in rows if row['event'] == 'dropped_evicted']
    emitted = [row for row in rows if row['event'] == 'emitted']
    submitted = [row for row in rows if row['event'] == 'submitted_store']
    admissions = [row for row in rows if row['event'] == 'admission']
    by_prefix = collections.defaultdict(lambda: {'admitted': 0, 'dropped': 0, 'emitted': 0, 'submitted': 0})
    for row in admissions: by_prefix[row['prefix_sha256']]['admitted'] += int(row['accepted'])
    for row in drops: by_prefix[row['prefix_sha256']]['dropped'] += 1
    for row in emitted: by_prefix[row['prefix_sha256']]['emitted'] += 1
    for row in submitted: by_prefix[row['prefix_sha256']]['submitted'] += 1
    danger = [row['danger_depth'] for row in rows if row['event'] == 'drain']
    output = dict(records=len(rows), event_counts=dict(counts),
                  dropped_evicted=len(drops), emitted=len(emitted),
                  submitted_store=len(submitted), admissions=len(admissions),
                  drain_steps=len(danger), danger_depth_mean=statistics.mean(danger) if danger else None,
                  drop_age_seconds_mean=statistics.mean([r['age_seconds'] for r in drops]) if drops else None,
                  prefix_outcomes=dict(by_prefix))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2) + '\n')
    print(json.dumps(output, indent=2))


if __name__ == '__main__': main()
