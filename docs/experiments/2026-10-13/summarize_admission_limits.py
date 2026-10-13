"""Describe archived diagnostics; never infer an unobserved pilot cause."""
import hashlib
import json
from pathlib import Path


def summarize(root):
    names = ['shared-structure-allocation-analysis.json', 'shared-structure-native-analysis.json']
    inputs = {name: (root / name).read_bytes() for name in names}
    allocation = json.loads(inputs[names[0]])
    native = json.loads(inputs[names[1]])['rows']
    rows = []
    for a, n in zip(allocation, native, strict=True):
        assert a['case'] == n['case']
        rows.append(dict(case=a['case'],
            reserve_over_pool_fraction=a['over_pool_steps'] / a['known_steps'],
            allocating_fraction_when_reserve_over_pool=a['over_pool_allocating_steps'] / a['over_pool_steps'],
            free_hit_touch_to_gross_new_ratio=n['free_hit_touch_blocks'] / n['gross_new_blocks'],
            zero_compute_calls_with_new_blocks=n['zero_compute_calls_with_new_blocks'],
            full_sequence_rejections=n['rejection_reasons']['full_sequence_gate']))
    return dict(input_sha256={name: hashlib.sha256(data).hexdigest() for name,data in inputs.items()},
        rows=rows, scope='Separate instrumented runs. Descriptive ratios only; no safe reserve or pilot causal inference.')


if __name__ == '__main__':
    print(json.dumps(summarize(Path(__file__).parent / 'evidence'), indent=2))
