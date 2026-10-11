import pytest

from scripts.generation_session_ledger import GenerationSessionLedger


def wid(seq, nonce='a' * 32, raw='same'):
    return f'cp1:7:{seq}:{nonce}:{raw}'


def test_late_old_store_and_receipt_cannot_touch_successor():
    ledger = GenerationSessionLedger()
    old = wid(1)
    new = wid(2, 'b' * 32)
    ledger.register('same', old)
    old_store = ledger.begin(old, 'pending_store')
    ledger.begin(old, 'lookup')
    ledger.terminal(next(t for t in ledger.generations[old].operations if t.kind == 'lookup'))
    ledger.register('same', new)
    new_lookup = ledger.begin(new, 'lookup')
    ledger.terminal(new_lookup)
    ledger.submit_store(old_store)
    ledger.terminal(old_store)
    ledger.seal(old)
    ledger.seal(new)
    sent = []
    assert ledger.submit_ends(sent.append) == [old, new]
    assert sent == [old, new]


def test_seal_blocks_new_work_but_does_not_complete_pending_work():
    ledger = GenerationSessionLedger()
    old = wid(1)
    ledger.register('same', old)
    ticket = ledger.begin(old, 'pending_store')
    ledger.seal(old)
    with pytest.raises(RuntimeError):
        ledger.begin(old, 'lookup')
    with pytest.raises(RuntimeError):
        ledger.terminal(ticket)
    assert not ledger.ready(old)


def test_failed_end_is_unresolved_and_does_not_claim_reclaim():
    ledger = GenerationSessionLedger()
    old = wid(1)
    ledger.register('same', old)
    lookup = ledger.begin(old, 'lookup')
    ledger.terminal(lookup)
    ledger.seal(old)
    assert ledger.submit_ends(lambda wire: (_ for _ in ()).throw(OSError(wire))) == []
    assert ledger.generations[old].end_state == 'unresolved'
    assert ledger.generations[old].error


def test_raw_id_reuse_requires_monotonic_sequence_and_exact_wire():
    ledger = GenerationSessionLedger()
    old = wid(3)
    ledger.register('same', old)
    with pytest.raises(RuntimeError):
        ledger.register('same', wid(2, 'b' * 32))
    with pytest.raises(ValueError):
        ledger.register('other', wid(4))


def test_multiple_pending_chunks_must_each_terminal_before_end():
    ledger = GenerationSessionLedger()
    old = wid(1)
    ledger.register('same', old)
    first = ledger.begin(old, 'pending_store')
    second = ledger.begin(old, 'pending_store')
    ledger.submit_store(first)
    ledger.terminal(first)
    ledger.seal(old)
    assert ledger.submit_ends(lambda _: None) == []
    ledger.drop_pending(second)
    assert ledger.submit_ends(lambda _: None) == [old]


def test_two_preemptions_preserve_lookup_route_and_all_store_owners():
    ledger = GenerationSessionLedger()
    a, b, c = (wid(n) for n in (1, 2, 3))
    ledger.register('same', a)
    lookup = ledger.begin(a, 'lookup')
    stores = []
    for wire in (a, b, c):
        ledger.register('same', wire)
        stores.append(ledger.begin(wire, 'pending_store'))
    assert ledger.lookup_routes['same'] == a
    assert ledger.current['same'] == c
    for wire in (a, b, c):
        ledger.seal(wire)
    for ticket in reversed(stores):
        ledger.submit_store(ticket)
        ledger.terminal(ticket)
    assert ledger.submit_ends(lambda _: pytest.fail('Unacked lookup')) == []
    ledger.terminal(lookup)
    assert ledger.submit_ends(lambda _: None) == [a, b, c]


def test_old_receipt_after_new_lookup_changes_only_old_ticket():
    ledger = GenerationSessionLedger()
    a, b = wid(1), wid(2)
    ledger.register('same', a)
    old = ledger.begin(a, 'pending_store')
    ledger.submit_store(old)
    ledger.register('same', b)
    lookup = ledger.begin(b, 'lookup')
    pending = ledger.begin(b, 'pending_store')
    before = dict(ledger.generations[b].operations)
    ledger.terminal(old)
    with pytest.raises(RuntimeError, match='duplicate'):
        ledger.terminal(old)
    assert ledger.generations[b].operations == before == {lookup: 'active', pending: 'pending'}
    assert ledger.lookup_routes['same'] == b


@pytest.mark.parametrize('kind', ['lookup', 'status', 'free', 'retrieve'])
def test_cancel_seal_waits_for_each_acquired_operation(kind):
    ledger = GenerationSessionLedger()
    wire = wid(1)
    ledger.register('same', wire)
    ticket = ledger.begin(wire, kind)
    ledger.seal(wire)
    assert not ledger.ready(wire)
    ledger.terminal(ticket)
    sent = []
    assert ledger.submit_ends(sent.append) == [wire]
    assert ledger.submit_ends(sent.append) == []
    assert sent == [wire]
    # A transport return does not establish server reclamation.
    assert ledger.generations[wire].end_state == 'submitted'


def test_high_water_fence_covers_other_raw_ids_and_unacked_lookup():
    ledger = GenerationSessionLedger()
    a, b = wid(1, raw='a'), wid(2, raw='b')
    ledger.register('a', a)
    ledger.register('b', b)
    ledger.seal(b)
    assert not ledger.ready(b)  # a could still submit its first lookup
    lookup = ledger.begin(a, 'lookup')
    ledger.seal(a)
    assert not ledger.ready(b)  # sealing does not acknowledge a lookup
    ledger.terminal(lookup)
    assert ledger.submit_ends(lambda _: None) == [a, b]
    late = wid(1, 'b' * 32, raw='late')
    with pytest.raises(RuntimeError, match='collision'):
        ledger.register('late', late)


def test_end_failure_after_possible_send_does_not_retry_or_block_other_end():
    ledger = GenerationSessionLedger()
    a, b = wid(1), wid(2)
    for wire in (a, b):
        ledger.register('same', wire)
        ledger.seal(wire)
    calls = []
    def send(wire):
        calls.append(wire)
        if wire == a:
            raise TimeoutError('possibly sent')
    assert ledger.submit_ends(send) == [b]
    assert ledger.submit_ends(send) == []
    assert calls == [a, b]
    assert ledger.generations[a].end_state == 'unresolved'
    assert ledger.high[7] == 2


def test_new_lookup_with_sequence_below_end_fence_is_rejected():
    ledger = GenerationSessionLedger()
    high, late = wid(5), wid(3, raw='late')
    ledger.register('same', high)
    ledger.seal(high)
    ledger.submit_ends(lambda _: None)
    ledger.register('late', late)
    with pytest.raises(RuntimeError, match='high water'):
        ledger.begin(late, 'lookup')
    assert ledger.lookup_routes == {}


def test_retired_identity_and_sequence_cannot_be_reused():
    ledger = GenerationSessionLedger()
    a, b = wid(1), wid(2)
    ledger.register('same', a)
    ledger.register('same', b)
    with pytest.raises(RuntimeError, match='Retired'):
        ledger.register('same', a)
    with pytest.raises(RuntimeError, match='collision'):
        ledger.register('same', wid(2, 'b' * 32))


def test_submitted_store_cannot_be_dropped_or_resubmitted():
    ledger = GenerationSessionLedger()
    wire = wid(1)
    ledger.register('same', wire)
    store = ledger.begin(wire, 'pending_store')
    ledger.submit_store(store)
    with pytest.raises(RuntimeError, match='submitted'):
        ledger.drop_pending(store)
    with pytest.raises(RuntimeError, match='non-pending'):
        ledger.submit_store(store)


def test_lifetime_bound_keeps_tombstones_instead_of_evicting_them():
    ledger = GenerationSessionLedger(capacity=1)
    wire = wid(1)
    ledger.register('same', wire)
    ledger.seal(wire)
    ledger.submit_ends(lambda _: None)
    with pytest.raises(RuntimeError, match='capacity'):
        ledger.register('same', wid(2))
    assert ledger.current['same'] == wire
    assert ledger.generations[wire].end_state == 'submitted'
