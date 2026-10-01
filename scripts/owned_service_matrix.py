"""Sequential real-service resource gates; each case owns and stops its services."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

from validate_environment import ROOT, write_json


CASES = {
    "l2-cancel": ("l2_prefetch_cancel_smoke.py", "--owned-service"),
    "l2-short-read": ("l2_prefetch_cancel_smoke.py", "--owned-service", "--truncate-index", "8"),
    "client-death": ("l2_prefetch_cancel_smoke.py", "--owned-service", "--crash-client",
                     "--observe-seconds", "150", "--registration-grace-seconds", "120"),
    "held-shutdown": ("l2_prefetch_cancel_smoke.py", "--owned-service", "--owned-shutdown"),
    "store-enqueue-error": ("owned_service_fault_smoke.py", "--kind", "store"),
    "retrieve-enqueue-error": ("owned_service_fault_smoke.py", "--kind", "retrieve"),
    "cancel-store-stream": ("owned_service_fault_smoke.py", "--kind", "store", "--cancel"),
    "cancel-retrieve-stream": ("owned_service_fault_smoke.py", "--kind", "retrieve", "--cancel"),
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cases", choices=list(CASES), nargs="+", default=list(CASES))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    results = []
    for name in args.cases:
        script, *options = CASES[name]
        out = args.output / name
        command = [sys.executable, str(ROOT / "scripts" / script), "--output", str(out), *options]
        print(f"Running {name}", flush=True)
        with (args.output / f"{name}.log").open("w") as stream:
            code = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT).returncode
        result_file = out / "result.json"
        result = json.loads(result_file.read_text()) if result_file.exists() else {}
        results.append(dict(case=name, returncode=code, passed=code == 0 and result.get("passed") is True,
                            error=result.get("error")))
        write_json(args.output / "summary.json", results)
        print(json.dumps(results[-1]), flush=True)
    return 0 if all(r["passed"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
