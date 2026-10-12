"""Offline structural admission audit, without model/service installation."""
import argparse
import json
from pathlib import Path
from run_real_structure_pilot import collect_structural


def main():
    p = argparse.ArgumentParser()
    p.add_argument('directory', type=Path)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    report = collect_structural(args.directory)
    args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({key: report[key] for key in ('matrix_complete', 'expected_cells', 'passed_cells')}))
    if not report['matrix_complete']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
