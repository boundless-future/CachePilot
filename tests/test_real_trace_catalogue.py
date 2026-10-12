import json

from scripts.catalogue_real_traces import inspect_trace


def sample(**changes):
    trace = dict(block_size=64, requests=[dict(t=i, **{'in': 1024}, hash_ids=['a']) for i in range(3)])
    trace.update(changes)
    return json.dumps(trace).encode()


def test_structural_selection_excludes_nested_and_oversized_sessions():
    good = inspect_trace('trace.json', sample())
    assert good['eligible'] and good['scaled_peak'] == 128
    nested = inspect_trace('trace.json', sample(requests=[
        dict(t=i, **{'in': 1024}, hash_ids=['a'], requests=[]) for i in range(3)]))
    assert nested['exclusion_reasons'] == ['nested_subagents']
    large = inspect_trace('trace.json', sample(requests=[
        dict(t=i, **{'in': 65536}, hash_ids=['a']) for i in range(3)]))
    assert large['exclusion_reasons'] == ['context_bound']


def test_missing_hash_and_empty_sessions_are_not_eligible():
    empty = inspect_trace('empty', sample(requests=[]))
    assert set(empty['exclusion_reasons']) == {'request_count', 'missing_hash_blocks'}
    missing = inspect_trace('missing', sample(requests=[dict(t=i, **{'in': 1}) for i in range(3)]))
    assert missing['exclusion_reasons'] == ['missing_hash_blocks']
