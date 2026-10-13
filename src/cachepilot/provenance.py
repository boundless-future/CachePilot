"""One source inventory for new experiment snapshots and offline verification."""
import hashlib
from pathlib import Path


def source_paths(root):
    root = Path(root)
    paths = set()
    for name in ("scripts", "configs"):
        paths.update(p for p in (root / name).glob("*")
                     if p.is_file() and p.suffix in {".py", ".sh", ".json"})
    for name, suffixes in (("src/cachepilot", {".py"}), ("native", {".cpp", ".h", ".hpp"})):
        paths.update(p for p in (root / name).rglob("*") if p.is_file() and p.suffix in suffixes)
    for name in ("pyproject.toml",):
        if (root / name).is_file():
            paths.add(root / name)
    return sorted(paths, key=lambda p: p.relative_to(root).as_posix())


def source_manifest(root):
    root = Path(root)
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in source_paths(root)}
