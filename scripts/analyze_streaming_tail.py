"""Offline streaming-duration observations; not CUDA per-token timings."""
import argparse
import json
from pathlib import Path
import statistics


def describe(values):
    if not values:
        raise ValueError('No successful streaming observations')
    return dict(mean=statistics.mean(values), p95=statistics.quantiles(
        values, n=100, method='inclusive')[94] if len(values) > 1 else values[0])


def analyze(data):
    spans, final_gaps, normalized = [], [], []
    for row in data['results']:
        if row.get('error') is not None:
            raise ValueError('Failed requests cannot be excluded from the comparison')
        chunks = row['chunk_arrivals_ms']
        if not chunks or chunks != sorted(chunks) or row['finished_ms'] < chunks[-1]:
            raise ValueError('Missing or unordered streaming timestamps')
        tokens = row['usage']['completion_tokens']
        if tokens < 2:
            raise ValueError('At least two output tokens required')
        span = chunks[-1] - chunks[0]
        spans.append(span)
        final_gaps.append(row['finished_ms'] - chunks[-1])
        normalized.append(span / (tokens - 1))
    return dict(requests=len(spans), first_to_last_fragment_ms=describe(spans),
        final_fragment_to_completion_ms=describe(final_gaps),
        stream_span_per_output_interval_ms=describe(normalized),
        scope='Observed nonempty SSE fragments; normalized by output tokens minus one. '
              'Fragments may contain multiple tokens; not per-token GPU or inter-token latency.')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('requests', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = analyze(json.loads(args.requests.read_text(encoding='utf-8')))
    args.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
