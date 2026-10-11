from types import SimpleNamespace as NS

import pytest
from scripts.idle_finalize import finalize_idle_pending, install_idle_scheduler
from scripts.owned_service_protocol import wire_id, CLIENT_CONFIG, SEQUENCE_CONFIG, GENERATION_CONFIG


@pytest.fixture
def case():
    meta = NS(request_id='r', request_configs={CLIENT_CONFIG: 7,
        SEQUENCE_CONFIG: 1, GENERATION_CONFIG: 'a'*32}, op=NS(start=0,end=1024))
    pending = {'r': [NS(store_metadata=meta)]}
    calls = []
    def drop(rid):
        calls.append(('drop',rid))
        return len(pending.pop(rid))
    client = NS(identities={'r':wire_id(meta)})
    def end(rid):
        calls.append(('end',client.identities.pop(rid)))
    connector = NS(_idle_finalize=True, _bounded=None, _handoffs=NS(pending={}),
        _lazy_offload_manager=NS(_requests=NS(in_flight_request_ids=lambda:set(),
            is_finished=lambda rid:True), _policy=NS(_pending=pending,drop_request=drop),
            _release_session=lambda rid:calls.append(('release',rid))),
        scheduler_adapter=NS(req_clients={'url':client},end_session=end),
        _handoff_record=lambda *a,**kw:None)
    scheduler=NS(connector=connector,requests={},running=[],waiting=[],skipped_waiting=[],deferred_frees={})
    return connector,scheduler,calls,meta


def test_drops_only_at_quiescence_and_no_duplicate_end(case):
    c,s,calls,meta=case
    assert finalize_idle_pending(c,s)==1
    assert calls==[('drop','r'),('release','r'),('end',wire_id(meta))]
    assert finalize_idle_pending(c,s)==0 and len(calls)==3


@pytest.mark.parametrize('field',['requests','running','waiting','skipped_waiting','deferred_frees'])
def test_paused_streaming_or_deferred_requests_are_not_idle(case,field):
    c,s,calls,_=case
    setattr(s,field,{'live':object()})
    assert finalize_idle_pending(c,s)==0 and not calls


@pytest.mark.parametrize('kind',['manager','handoff','prepared','protected'])
def test_inflight_or_prepared_work_prevents_all_drops(case,kind):
    c,s,calls,_=case
    if kind=='manager': c._lazy_offload_manager._requests.in_flight_request_ids=lambda:{'other'}
    elif kind=='handoff': c._handoffs.pending['other']=object()
    else: c._bounded=NS(prepared={'r':1} if kind=='prepared' else {},inflight={'r':1} if kind=='protected' else {})
    assert finalize_idle_pending(c,s)==0 and not calls


@pytest.mark.parametrize('mutation',['identity','unfinished','mixed'])
def test_generation_or_finished_mismatch_fails_before_mutation(case,mutation):
    c,s,calls,meta=case
    if mutation=='identity': c.scheduler_adapter.req_clients['url'].identities['r']='new generation'
    elif mutation=='unfinished': c._lazy_offload_manager._requests.is_finished=lambda rid:False
    else:
        from copy import deepcopy
        other=deepcopy(meta); other.request_configs[SEQUENCE_CONFIG]=2
        c._lazy_offload_manager._policy._pending['r'].append(NS(store_metadata=other))
    with pytest.raises(RuntimeError): finalize_idle_pending(c,s)
    assert not calls


def test_hook_preserves_busy_and_disabled_behavior(case):
    c,s,calls,_=case
    class Scheduler:
        def has_requests(self): return self.busy
    install_idle_scheduler(Scheduler); install_idle_scheduler(Scheduler)
    target=Scheduler(); target.__dict__.update(s.__dict__)
    target.busy=True
    assert target.has_requests() and not calls
    target.busy=False; c._idle_finalize=False
    assert not target.has_requests() and not calls
    c._idle_finalize=True
    assert not target.has_requests() and len(calls)==3


def test_grace_timeout_runs_on_consumer_and_does_not_spin(case):
    import queue
    from scripts.idle_finalize import IdleInputQueue
    c,s,calls,_=case
    c._idle_grace_seconds=0.001
    core=NS(scheduler=s,is_running=lambda:True,has_work=lambda:False)
    q=IdleInputQueue(queue.Queue(),core)
    with pytest.raises(queue.Empty): q.get()
    assert len(calls)==3
    q.put('shutdown')
    assert q.get()=='shutdown' and len(calls)==3


def test_arrival_during_grace_wins_and_shutdown_prevents_drop(case):
    import queue
    from scripts.idle_finalize import IdleInputQueue
    c,s,calls,_=case; c._idle_grace_seconds=0.001
    core=NS(scheduler=s,is_running=lambda:False,has_work=lambda:False)
    q=IdleInputQueue(queue.Queue(),core)
    q.put(('new',object()))
    assert q.get()[0]=='new' and not calls
    with pytest.raises(queue.Empty): q.get()
    assert not calls


def test_grace_keeps_scheduler_predicate_passive(case):
    c,s,calls,_=case; c._idle_grace_seconds=15
    class Scheduler:
        def has_requests(self): return False
    install_idle_scheduler(Scheduler)
    target=Scheduler(); target.__dict__.update(s.__dict__)
    assert not target.has_requests() and not calls


@pytest.mark.parametrize('item', ['abort', 'shutdown', 'utility', 'new_request'])
def test_control_and_request_input_interrupt_grace_unchanged(case, item):
    from scripts.idle_finalize import IdleInputQueue
    c,s,calls,_=case; c._idle_grace_seconds=15
    payload = (item, object())
    class Queue:
        def get(self, *, block, timeout):
            assert block and timeout == 15
            return payload
    q=IdleInputQueue(Queue(),NS(scheduler=s))
    assert q.get() is payload and not calls


@pytest.mark.parametrize('kwargs', [{'block':False}, {'timeout':0.5}])
def test_explicit_queue_timeout_and_nonblocking_calls_delegate(case, kwargs):
    from scripts.idle_finalize import IdleInputQueue
    c,s,calls,_=case; c._idle_grace_seconds=15
    seen=[]
    class Queue:
        def get(self, **args):
            seen.append(args)
            return 'original'
    q=IdleInputQueue(Queue(),NS(scheduler=s))
    assert q.get(**kwargs) == 'original' and not calls
    assert seen == [dict(block=kwargs.get('block', True), timeout=kwargs.get('timeout'))]


@pytest.mark.parametrize('busy,arrived', [(True,False), (False,True)])
def test_timeout_rechecks_work_and_arrivals(case,busy,arrived):
    import queue
    from scripts.idle_finalize import IdleInputQueue
    c,s,calls,_=case; c._idle_grace_seconds=15
    class Queue:
        def get(self, **kwargs): raise queue.Empty
        def empty(self): return not arrived
    core=NS(scheduler=s,is_running=lambda:True,has_work=lambda:busy)
    with pytest.raises(queue.Empty): IdleInputQueue(Queue(),core).get()
    assert not calls


def test_all_pending_identities_checked_before_first_drop(case):
    from copy import deepcopy
    c,s,calls,meta=case
    other=deepcopy(meta); other.request_id='other'
    c._lazy_offload_manager._policy._pending['other']=[NS(store_metadata=other)]
    c.scheduler_adapter.req_clients['url'].identities['other']='stale'
    with pytest.raises(RuntimeError,match='current wire identity'):
        finalize_idle_pending(c,s)
    assert not calls and len(c._lazy_offload_manager._policy._pending)==2


def test_end_failure_surfaces_and_does_not_claim_drop_completed(case):
    c,s,calls,_=case
    events=[]
    c._handoff_record=lambda event,**kwargs:events.append(event)
    def fail(rid): raise RuntimeError('END unavailable')
    c.scheduler_adapter.end_session=fail
    with pytest.raises(RuntimeError,match='END unavailable'):
        finalize_idle_pending(c,s)
    assert events==['idle_pending_drop_planned']
    assert calls==[('drop','r'),('release','r')]
