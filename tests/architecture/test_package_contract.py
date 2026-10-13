"""Package boundaries and compatibility contracts for the runtime migration."""
import ast
import importlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from cachepilot.config import SharedPrefixConfig
from cachepilot.provenance import source_manifest

ROOT = Path(__file__).resolve().parents[2]
MAPPING = json.loads((ROOT / "docs/module-migration.json").read_text())


def test_runtime_never_imports_research_scripts():
    names = set(MAPPING)
    for path in (ROOT / "src/cachepilot").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom):
                targets = [node.module or ""]
            elif isinstance(node, ast.Import):
                targets = [a.name for a in node.names]
            else:
                continue
            assert all(t.split(".")[0] not in names | {"scripts"} for t in targets), str(path)


def test_mainline_tests_import_package_directly():
    for path in (ROOT / "tests").glob("test_*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom):
                assert (node.module or "").removeprefix("scripts.") not in MAPPING, str(path)


def test_package_configured_without_legacy_path_bootstrap(pytestconfig):
    assert pytestconfig.inipath.name == "pyproject.toml"
    assert ROOT / "src" in pytestconfig.getini("pythonpath")


@pytest.mark.parametrize("old,new", MAPPING.items())
def test_legacy_and_package_imports_have_same_identity(old, new):
    try:
        canonical = importlib.import_module("cachepilot." + new)
    except ModuleNotFoundError as error:
        if error.name.split(".")[0] in {"vllm", "lmcache", "msgspec", "cachepilot_reservation_native"}:
            pytest.skip(f"Fixed-stack dependency unavailable: {error.name}")
        raise
    legacy = importlib.import_module("scripts." + old)
    assert legacy is canonical
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        assert importlib.import_module(old) is canonical
    finally:
        sys.path.remove(str(ROOT / "scripts"))


@pytest.mark.parametrize("budget,mode", [(0, "zero"), (1, "one"), (2, "two")])
def test_unified_config_matches_existing_matrix(budget, mode):
    # Preserve the experiment's settings except for the canonical module path.
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        from scripts.run_shared_strategy_matrix import configure
        base = json.loads((ROOT / "configs/protection-zero.json").read_text())
        previous = configure(base, dict(kind="lazy", horizon=5.0, drain=1, mode=mode))
    finally:
        sys.path.remove(str(ROOT / "scripts"))
    previous["kv_connector_module_path"] = "cachepilot.integration.vllm.shared_prefix"
    assert SharedPrefixConfig(max_chunks=budget).kv_transfer_config() == previous


@pytest.mark.parametrize("kwargs", [dict(max_chunks=True), dict(max_chunks=3),
    dict(horizon_steps=float("nan")), dict(horizon_steps=0), dict(port=0),
    dict(idle_grace_seconds=-1), dict(max_drain_per_step=0)])
def test_invalid_config_fails_before_gpu_import(kwargs):
    with pytest.raises(ValueError):
        SharedPrefixConfig(**kwargs)


def test_lightweight_entry_does_not_import_gpu_stack():
    code = "import cachepilot.cli,sys; cachepilot.cli.main(['config']); assert not {'torch','vllm','lmcache'} & sys.modules.keys()"
    import os
    environment = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
    subprocess.run([sys.executable, "-c", code], env=environment, check=True, capture_output=True)


def test_manifest_tracks_nested_runtime_native_and_build_metadata(tmp_path):
    files = ["src/cachepilot/runtime/protection.py", "native/reservation_lock.cpp", "pyproject.toml"]
    for name in files:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("original")
    before = source_manifest(tmp_path)
    assert set(before) == set(files)
    (tmp_path / files[0]).write_text("changed")
    assert source_manifest(tmp_path)[files[0]] != before[files[0]]
    from scripts.verify_source_snapshot import verify
    plan = {"signature": {"source_sha256": before}}
    assert not verify(plan, tmp_path)["passed"]
    extra = tmp_path / "src/cachepilot/runtime/new.py"
    extra.write_text("new")
    assert verify(plan, tmp_path)["additional_files"] == ["src/cachepilot/runtime/new.py"]
