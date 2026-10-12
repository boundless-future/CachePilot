import copy
from pathlib import Path
import sys
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from run_generation_strategy_formal import CASES, ADDITIONS, order, check_sources
from audit_generation_strategy_formal import aggregate


def test_preserve_all_cases_and_rotate():
    assert [order(i)[0] for i in range(3)] == ['default-zero','selected-zero','selected-one']
    assert all(set(order(i)) == set(CASES) for i in range(3))


@pytest.mark.parametrize('change', ['none','runtime','missing','extra'])
def test_only_explicit_harness_additions_allowed(change):
    previous={'scripts/connector.py':'frozen'}
    current=dict(previous, **{p:'new' for p in ADDITIONS})
    if change=='runtime': current['scripts/connector.py']='different'
    if change=='missing': current.pop('scripts/connector.py')
    if change=='extra': current['scripts/unlisted.py']='extra'
    if change=='none': check_sources(previous,current)
    else:
        with pytest.raises(ValueError): check_sources(previous,current)


@pytest.mark.parametrize('change', ['none','failed','absent','duplicate'])
def test_no_partial_or_duplicate_means(change):
    rows=[dict(case=name, repeat=i, passed=True, ttft_p95_ms=100+i,
        h2d_bytes=10,d2h_bytes=20,prefill_tokens=30,committed=1) for i in range(3) for name in CASES]
    if change=='failed': rows[0]['passed']=False
    if change=='absent': rows.pop()
    if change=='duplicate': rows[-1]=copy.deepcopy(rows[0])
    result=aggregate(rows)
    assert bool(result)==(change=='none')
    if result:
        assert result['selected-one']['ttft_p95_ms']==dict(mean=101,stdev=1.0,individual=[100,101,102])
