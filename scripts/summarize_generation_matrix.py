"""Offline entrypoint for independently auditing generation-stack matrices."""
import argparse
import json
from pathlib import Path
from run_generation_matrix import collect


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('directory', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = collect(args.directory)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: report[k] for k in ('matrix_complete', 'expected_cells', 'passed_cells')}))
    for row in report['rows']:
        if not row['passed']:
            print(row['cell'], row.get('error'), row.get('generation_error'),
                  {k: v for k, v in row.get('checks', {}).items() if not v})
    if not report['matrix_complete']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
