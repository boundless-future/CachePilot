"""Verify raw bytes and repeat matrix audit with current and frozen sources.

Usage: python verify_shared_structure_allocation.py ARCHIVE OUTPUT_DIRECTORY
Runs only offline readers; a failed experiment may have a successful integrity audit.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tarfile


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('archive', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    raw = output / 'raw'
    with tarfile.open(args.archive) as archive:
        archive.extractall(raw, filter='data')
    inventory = read(raw / 'raw-inventory.json')
    for name, entry in inventory.items():
        value = (raw / name).read_bytes()
        assert len(value) == entry['bytes'], name
        assert hashlib.sha256(value).hexdigest() == entry['sha256'], name
    assert {p.relative_to(raw).as_posix() for p in raw.rglob('*') if p.is_file()} == set(inventory) | {'raw-inventory.json'}
    root = Path(__file__).resolve().parents[3]
    matrices = []
    for plan_path in sorted((raw / 'artifacts').glob('shared-structure-allocation-*/plan.json')):
        matrix = plan_path.parent
        plan = read(plan_path)
        frozen = output / (matrix.name + '-source')
        with tarfile.open(matrix / 'frozen-source.tar.gz') as archive:
            archive.extractall(frozen, filter='data')
        expected = plan['signature']['source_sha256']
        assert {p.relative_to(frozen).as_posix() for p in frozen.rglob('*') if p.is_file()} == set(expected)
        for name, sha in expected.items():
            assert hashlib.sha256((frozen / name).read_bytes()).hexdigest() == sha, name
        reports = []
        for label, source_root in (('local', root), ('frozen', frozen)):
            destination = output / (matrix.name + '-' + label + '.json')
            proc = subprocess.run([sys.executable, str(source_root / 'scripts/audit_shared_structure_allocation.py'),
                str(matrix), '--output', str(destination)], capture_output=True, text=True, encoding='utf-8')
            (output / (matrix.name + '-' + label + '-audit.txt')).write_text(proc.stdout + proc.stderr, encoding='utf-8')
            assert proc.returncode in (0, 1) and destination.exists(), proc.stderr
            report = read(destination)
            assert proc.returncode == (0 if report['diagnostic_passed'] else 1)
            reports.append(report)
        server = read(matrix / 'audit.json')
        assert reports[0] == reports[1] == server, matrix.name
        matrices.append(dict(name=matrix.name, source_count=len(expected), diagnostic_passed=server['diagnostic_passed'],
            passed_cells=sum(r['diagnostic_passed'] for r in server['rows']), local_server_frozen_equal=True))
    assert matrices, 'No matrix found in archive'
    result = dict(integrity_passed=True, files=len(inventory), raw_bytes=sum(x['bytes'] for x in inventory.values()),
        archive_sha256=hashlib.sha256(args.archive.read_bytes()).hexdigest(), matrices=matrices)
    (output / 'integrity.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
