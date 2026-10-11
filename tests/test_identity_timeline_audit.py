import json
import pytest
from scripts.analyze_identity_timeline import analyze


@pytest.fixture
def evidence(tmp_path):
    folder=tmp_path/'identity-observation'
    folder.mkdir()
    scheduler=[dict(event='on_request_reset_after',request_id='r',pid=1,monotonic_ns=1),
        dict(event='tracker_created',request_id='r',wire_id='new',replaced=True,
             request_status='PREEMPTED',pid=1,monotonic_ns=2),
        dict(event='add_store_candidate_after',request_id='r',wire_id='new',
             mappings={'url':{'r':'old'}},pid=1,monotonic_ns=3),
        dict(event='end_session_before',current='old',pid=1,monotonic_ns=4),
        dict(event='before_shutdown',pid=1,monotonic_ns=5)]
    worker=[dict(event='store_after',wire_id='new',pid=2,monotonic_ns=3)]
    result=dict(passed=False,requests=68,vllm_delta={'vllm:num_preemptions_total':1},
                status_before_engine_stop={'active_sessions':1},service_errors=[])
    def save():
        for name,rows in [('SCHEDULER',scheduler),('WORKER',worker)]:
            (folder/f'timeline-{name}.jsonl').write_text(
                '\n'.join(json.dumps(r) for r in rows)+'\n')
        (tmp_path/'result.json').write_text(json.dumps(result))
    return tmp_path,scheduler,worker,save


def test_diagnosis_retains_original_failed_gate(evidence):
    path,s,w,save=evidence
    save()
    report=analyze(path)
    assert report['diagnostic_chain_observed']
    assert not report['original_cell_passed'] and not report['idle_exception_reproduced']
    assert report['unended_store_identities']==['new']


@pytest.mark.parametrize('mutation',['no_reset','not_preempted','current_mapping','truncated'])
def test_rejects_incomplete_or_contradictory_chain(evidence,mutation):
    path,s,w,save=evidence
    if mutation=='no_reset': s.pop(0)
    elif mutation=='not_preempted': s[1]['request_status']='WAITING'
    elif mutation=='current_mapping': s[2]['mappings']['url']['r']='new'
    else: s.pop()
    save()
    with pytest.raises(AssertionError): analyze(path)
