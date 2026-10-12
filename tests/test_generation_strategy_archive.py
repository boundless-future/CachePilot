import json
from pathlib import Path
import shutil
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from run_generation_strategy_pilot import audit_cell, CASES
from audit_replay_measurements import audit_cell as timing_audit

SOURCE = Path(__file__).resolve().parents[1] / 'docs/experiments/2026-10-12/generation-strategy-evidence/generation-strategy-pilot-20261012b'


@pytest.mark.parametrize('change', ['none', 'unclosed', 'early_end', 'runtime_error', 'latency'])
def test_raw_archive_cannot_be_promoted_by_saved_pass_flag(tmp_path, change):
    cell = tmp_path / 'default-zero-r0'
    shutil.copytree(SOURCE / cell.name, cell)
    if change in ('unclosed', 'early_end'):
        file = cell / 'generation-summary.json'
        data = json.loads(file.read_text())
        if change == 'unclosed':
            data['pending_rpc'] = 1
        else:
            data['generations'][0]['end_state'] = 'pending'
        file.write_text(json.dumps(data))
    elif change == 'runtime_error':
        with (cell / 'vllm.log').open('a') as stream:
            stream.write('\nERROR unexpected runtime failure\n')
    elif change == 'latency':
        file = cell / 'result.json'
        data = json.loads(file.read_text())
        data['ttft_ms']['p95'] *= 0.5
        file.write_text(json.dumps(data))
    plan = json.loads((SOURCE / 'plan.json').read_text())
    trace = json.loads((cell / 'trace.json').read_text())
    passed = audit_cell(cell, CASES['default-zero'], plan['signature'], trace)['passed']
    passed &= timing_audit(cell)['passed']
    assert passed == (change == 'none')
