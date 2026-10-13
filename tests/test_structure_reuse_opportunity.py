import importlib.util
from pathlib import Path
import pytest

path=Path(__file__).resolve().parents[1]/'docs/experiments/2026-10-13/analyze_structure_reuse_opportunity.py'
spec=importlib.util.spec_from_file_location('structure_reuse_opportunity',path)
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def fixture():
    trace={'events':[dict(event_id=i,session_id='s',prompt=[1]*512) for i in range(3)]}
    requests={'results':[dict(event_id=i,error=None,sent_ms=s,finished_ms=f)
                        for i,(s,f) in enumerate(((0,10),(5,15),(20,30)))]}
    return trace,requests


def test_already_sent_overlap_is_not_post_completion_demand():
    result=module.analyze(*fixture())
    assert result['summary']['1']['requests_with_later_matching_demand']==2
    assert result['summary']['2']['unique_prefixes_with_later_demand']==1
    assert [r['event_id'] for r in result['rows'][0]['prefixes'][0]['later_after_completion']]==[2]


def test_shorter_future_request_and_prefix_change():
    t,r=fixture();t['events'][2]['prompt']=[1]*256
    result=module.analyze(t,r)
    assert result['summary']['1']['requests_with_later_matching_demand']==2
    assert result['summary']['2']['requests_with_later_matching_demand']==0
    t['events'][2]['prompt'][0]=2
    assert module.analyze(t,r)['summary']['1']['requests_with_later_matching_demand']==0


@pytest.mark.parametrize('change',['duplicate','missing','error','text'])
def test_rejects_incomplete_or_unmapped_data(change):
    t,r=fixture()
    if change=='duplicate':r['results'][1]['event_id']=0
    if change=='missing':r['results'].pop()
    if change=='error':r['results'][1]['error']='timeout'
    if change=='text':t['events'][0]['prompt']='not tokenized'
    with pytest.raises(ValueError):module.analyze(t,r)
