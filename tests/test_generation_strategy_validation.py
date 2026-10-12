import copy
from pathlib import Path
import sys
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from audit_generation_strategy_validation import input_audit, read
from run_generation_strategy_validation import ADDITIONS, check_sources

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'docs/experiments/2026-10-12'


@pytest.mark.parametrize('mutation', ['none', 'arrival', 'length', 'overlap'])
def test_frozen_inputs_cannot_be_retuned(mutation):
    old = read(BASE / 'generation-strategy-formal-evidence/generation-strategy-formal-20261012a/trace.json')
    new = read(BASE / 'generation-strategy-validation-evidence/validation-trace.json')
    if mutation == 'arrival': new['events'][-1]['arrival_ms'] += 1
    if mutation == 'length': new['events'][0]['prompt_tokens'] += 1
    if mutation == 'overlap': new = copy.deepcopy(old)
    result = input_audit(old, new)
    assert result['passed'] == (mutation == 'none')
    if mutation == 'none':
        assert result['validation_prompt_tokens'] == 75808
        assert result['development_prompt_tokens'] == 74652


@pytest.mark.parametrize('mutation', ['none', 'runtime', 'addition', 'missing'])
def test_validation_runtime_must_match_development(mutation):
    old = {'scripts/connector.py': 'original'}
    new = dict(old, **{p:'new' for p in ADDITIONS})
    if mutation == 'runtime': new['scripts/connector.py'] = 'edited'
    if mutation == 'addition': new['scripts/unknown.py'] = 'extra'
    if mutation == 'missing': new.pop('scripts/connector.py')
    if mutation == 'none': check_sources(old, new)
    else:
        with pytest.raises(ValueError): check_sources(old, new)


def test_real_zero_coverage_archive_is_not_promoted(tmp_path):
    import json
    import shutil
    from audit_generation_strategy_validation import audit
    source = BASE / 'generation-strategy-validation-evidence/generation-strategy-validation-pilot-20261012a'
    target = tmp_path / 'pilot'
    shutil.copytree(source, target)
    path = target / 'selected-one-r0/result.json'
    result = read(path)
    result['passed'] = True
    path.write_text(json.dumps(result))
    report = audit(target)
    assert report['passed_cells'] == 2
    assert not report['matrix_complete'] and report['aggregates'] == {}
    failed = next(r for r in report['rows'] if r['case'] == 'selected-one')
    assert failed['checks']['resources'] and failed['checks']['generations']
    assert not failed['checks']['coverage']
