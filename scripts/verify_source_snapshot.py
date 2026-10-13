"""Verify saved runtime bytes against an experiment's frozen manifest, offline."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

_src = str(Path(__file__).resolve().parents[1] / "src")
if _src not in sys.path:
    sys.path.insert(0, _src)
from cachepilot.provenance import source_paths


def verify(plan, root):
    expected = plan["signature"]["source_sha256"]
    root = root.resolve()
    failures = []
    for relative, sha in expected.items():
        path = (root / relative).resolve()
        if not path.is_relative_to(root):
            failures.append(dict(path=relative, reason="outside source root"))
            continue
        if not path.is_file():
            failures.append(dict(path=relative, reason="missing source"))
            continue
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != sha:
            failures.append(dict(path=relative, reason="bytes differ", expected=sha, actual=actual))
    extra = [path.relative_to(root).as_posix() for path in source_paths(root)
             if path.relative_to(root).as_posix() not in expected]
    return dict(passed=bool(expected) and not failures, expected_files=len(expected), failures=failures,
        additional_files=extra, scope="Exact bytes of manifest-listed sources; additions reported separately")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("plan", type=Path)
    parser.add_argument("source_root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = verify(json.loads(args.plan.read_text(encoding="utf-8")), args.source_root)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("passed", "expected_files", "failures")}))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
