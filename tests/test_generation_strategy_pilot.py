import copy
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from run_generation_strategy_pilot import CASES, strategy_config, collect, TRACE_SHA


def test_matched_candidates_change_only_chunk_budget():
    original = {'kv_connector_extra_config': {'sentinel': 9}}
    untouched = copy.deepcopy(original)
    configs = [strategy_config(original, CASES['selected-' + mode]) for mode in ('zero', 'one', 'two')]
    assert original == untouched
    for budget, config in enumerate(configs):
        extra = config['kv_connector_extra_config']
        assert extra.pop('cachepilot.protection.max_chunks') == budget
        assert extra['lmcache.mp.lazy_offload_horizon_steps'] == 5.0
        assert extra['lmcache.mp.lazy_offload_max_drain_per_step'] == 1
        assert extra['cachepilot.generation_ledger'] is True
        assert extra['cachepilot.generation_protection'] is True
        assert config['kv_connector'] == 'GenerationProtectionConnector'
    assert configs[0] == configs[1] == configs[2]


@pytest.mark.parametrize('failure', ['absent', 'coverage', 'none'])
def test_incomplete_pilot_has_no_comparisons(monkeypatch, tmp_path, failure):
    import run_generation_strategy_pilot as runner
    monkeypatch.setattr(runner, 'read', lambda path: {'cases': CASES, 'signature': {}} if path.name == 'plan.json' else {})
    monkeypatch.setattr(runner, 'digest', lambda trace: TRACE_SHA)
    def audit(cell, case, signature, trace):
        if cell.name == 'selected-one-r0':
            if failure == 'absent':
                raise FileNotFoundError('not run')
            if failure == 'coverage':
                return dict(passed=False, ttft_p95_ms=100)
        return dict(passed=True, ttft_p95_ms=100)
    monkeypatch.setattr(runner, 'audit_cell', audit)
    result = collect(tmp_path)
    assert result['matrix_complete'] == (failure == 'none')
    assert bool(result['relative_selected_zero_pct']) == (failure == 'none')


def test_reject_modified_trace(monkeypatch, tmp_path):
    import run_generation_strategy_pilot as runner
    monkeypatch.setattr(runner, 'read', lambda path: {'cases': CASES} if path.name == 'plan.json' else {})
    monkeypatch.setattr(runner, 'digest', lambda trace: 'changed')
    with pytest.raises(ValueError, match='trace changed'):
        collect(tmp_path)


def test_root_shadow_cannot_override_script_dependencies(tmp_path):
    import shutil
    import subprocess
    scripts = tmp_path / 'scripts'
    scripts.mkdir()
    shutil.copy(Path(__file__).resolve().parents[1] / 'scripts/run_generation_strategy_pilot.py', scripts)
    (tmp_path / 'run_fixed_baselines.py').write_text('raise RuntimeError("stale root shadow")')
    (scripts / 'run_fixed_baselines.py').write_text(
        'configure=read=KV_BYTES=strict_idle=select_fixed=validate_completed=None\nCASES={}\n')
    (scripts / 'run_generation_matrix.py').write_text('extra_checks=None\n')
    (scripts / 'run_arrival_matrix.py').write_text('digest=None\n')
    result = subprocess.run([sys.executable, '-c',
        'import runpy; runpy.run_path("scripts/run_generation_strategy_pilot.py", run_name="audit_import")'],
        cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
