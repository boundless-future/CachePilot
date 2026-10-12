"""Admission rejects provenance drift; absent gauges cannot prove low pressure."""
import importlib
import copy
import json
from pathlib import Path

import pytest


@pytest.fixture
def runner(monkeypatch):
    root = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(root / 'scripts'))
    return importlib.import_module('run_real_structure_pilot')


def test_missing_gauges_remain_unknown(runner):
    report = runner.pressure_summary([dict(metrics='unrelated_counter 4\n'), dict(error='timeout')])
    assert not report['all_gauges_observed']
    assert report['errors'] == 1
    assert all(value['peak'] is None for value in report['observed_metrics'].values())


def test_zero_usage_is_a_real_observation_not_a_missing_value(runner):
    report = runner.pressure_summary([dict(metrics='\n'.join([
        'vllm:kv_cache_usage_perc{engine="0"} 0',
        'vllm:num_requests_running{engine="0"} 6',
        'vllm:num_requests_waiting{engine="0"} 2',
    ])), dict(metrics='vllm:kv_cache_usage_perc{engine="0"} 0.75')])
    assert report['all_gauges_observed']
    usage = report['observed_metrics']['vllm:kv_cache_usage_perc']
    assert usage == dict(samples=2, peak=0.75)


def test_actual_model_tokenizer_bytes_are_required(runner, tmp_path):
    root = Path(__file__).resolve().parents[1] / 'docs/experiments/2026-10-12'
    manifest = json.loads((root / 'real-structure-built/manifest.json').read_text())
    for name in manifest['tokenizer_sha256']:
        (tmp_path / name).write_text('different tokenizer')
    with pytest.raises(ValueError, match='Tokenizer source changed'):
        runner.validate_inputs(root / 'real-structure-built', root / 'real-structure-source',
            root / 'REAL_STRUCTURE_CATALOGUE.json', tmp_path)


def test_unknown_or_nonzero_preemptions_reject_immediate(runner):
    assert not runner.immediate_supported(dict(vllm_delta={}))
    assert not runner.immediate_supported(dict(vllm_delta={'vllm:num_preemptions_total': 1}))
    assert runner.immediate_supported(dict(vllm_delta={'vllm:num_preemptions_total': 0}))


@pytest.mark.parametrize('mutation', ['none', 'sample', 'resource'])
def test_offline_admission_recomputes_observer_and_keeps_resource_failure(runner, monkeypatch, tmp_path, mutation):
    """A recorded pass flag or peak cannot override raw samples or base gates."""
    root = Path(__file__).resolve().parents[1] / 'docs/experiments/2026-10-12'
    import shutil
    (tmp_path / 'input').mkdir()
    shutil.copytree(root / 'real-structure-built', tmp_path / 'input/built')
    shutil.copytree(root / 'real-structure-source', tmp_path / 'input/source')
    shutil.copyfile(root / 'REAL_STRUCTURE_CATALOGUE.json', tmp_path / 'input/catalogue.json')
    shutil.copyfile(root / 'real-structure-built/trace.json', tmp_path / 'trace.json')
    provenance = runner.audit(tmp_path / 'input/built', tmp_path / 'input/source', tmp_path / 'input/catalogue.json')
    (tmp_path / 'provenance-audit.json').write_text(json.dumps(provenance))
    report = dict(signature=dict(trace_sha256=runner.TRACE_SHA),
        rows=[dict(cell='native-r0', case='native', passed=mutation != 'resource', checks={})],
        aggregates={'native': {'untrusted': 'must not survive'}}, output_comparisons=[])
    monkeypatch.setattr(runner, 'collect', lambda _: copy.deepcopy(report))
    cell = tmp_path / 'native-r0'
    cell.mkdir()
    samples = [dict(metrics='\n'.join(name + ' 0' for name in runner.GAUGES))]
    pressure = runner.pressure_summary(samples)
    (cell / 'pressure-summary.json').write_text(json.dumps(pressure))
    if mutation == 'sample':
        samples[0]['metrics'] = 'vllm:kv_cache_usage_perc 0.8'
    (cell / 'pressure-samples.json').write_text(json.dumps(samples))
    (cell / 'result.json').write_text(json.dumps(dict(vllm_delta={})))
    actual = runner.collect_structural(tmp_path)
    assert actual['matrix_complete'] == (mutation == 'none')
    assert actual['aggregates'] == {}
