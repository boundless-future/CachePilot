"""Byte inventory for a completed archived experiment (no service changes)."""
import argparse
import hashlib
import json
from pathlib import Path


def inventory(directory):
    if not directory.is_dir():
        raise ValueError('Experiment directory required')
    return {path.relative_to(directory).as_posix(): dict(
        bytes=path.stat().st_size, sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        for path in sorted(directory.rglob('*')) if path.is_file()}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('directory', type=Path)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    if a.output.resolve().is_relative_to(a.directory.resolve()):
        p.error('Inventory must be outside the inventoried directory')
    a.output.write_text(json.dumps(inventory(a.directory), indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
