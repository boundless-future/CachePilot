import pytest
from scripts.analyze_streaming_tail import analyze


def test_chunk_coalescing_is_not_assumed_one_token_per_fragment():
    data = dict(results=[dict(error=None, chunk_arrivals_ms=[100, 130, 190],
        finished_ms=195, usage=dict(completion_tokens=10))])
    result = analyze(data)
    assert result['first_to_last_fragment_ms']['mean'] == 90
    assert result['stream_span_per_output_interval_ms']['mean'] == 10
    assert result['final_fragment_to_completion_ms']['mean'] == 5


@pytest.mark.parametrize('change', ['failure', 'order', 'finish', 'empty', 'nan'])
def test_invalid_streams_are_not_silently_filtered(change):
    row = dict(error=None, chunk_arrivals_ms=[100, 130, 190],
        finished_ms=195, usage=dict(completion_tokens=10))
    if change == 'failure':
        row['error'] = 'disconnected'
    elif change == 'order':
        row['chunk_arrivals_ms'] = [100, 190, 130]
    elif change == 'finish':
        row['finished_ms'] = 189
    elif change == 'empty':
        row['chunk_arrivals_ms'] = []
    else:
        row['finished_ms'] = float('nan')
    with pytest.raises(ValueError):
        analyze(dict(results=[row]))
