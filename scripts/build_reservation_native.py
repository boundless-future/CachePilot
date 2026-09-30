"""Build an isolated CPU extension using pybind11 headers bundled with torch."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import sysconfig

ROOT = Path(__file__).resolve().parents[1]


def main():
    if sys.platform != "linux":
        raise SystemExit("This experimental build currently supports Linux only")
    spec = importlib.util.find_spec("torch")
    if spec is None:
        raise SystemExit("Pinned environment with torch headers required")
    include = Path(spec.origin).parent / "include"
    if not (include / "pybind11/pybind11.h").is_file():
        raise SystemExit("Missing bundled pybind11 headers")
    source = ROOT / "native/reservation_lock.cpp"
    output = ROOT / "artifacts/reservation-native"
    output.mkdir(parents=True, exist_ok=True)
    target = output / ("cachepilot_reservation_native" + sysconfig.get_config_var("EXT_SUFFIX"))
    pending = target.with_suffix(target.suffix + ".pending")
    command = ["g++", "-O2", "-Wall", "-Wextra", "-Werror", "-std=c++17", "-shared",
               "-fPIC", "-pthread", "-I" + sysconfig.get_path("include"),
               "-I" + str(include), str(source), "-o", str(pending)]
    subprocess.run(command, check=True)
    pending.replace(target)
    manifest = dict(source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                    binary_sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
                    binary=target.name, python=sys.version, command=command,
                    compiler=subprocess.check_output(["g++", "--version"], text=True).splitlines()[0])
    (output / "build.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
