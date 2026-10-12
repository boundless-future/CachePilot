import json
from pathlib import Path
import shutil
import pytest
from scripts.audit_generation_protection_fault import audit

SOURCE = Path(__file__).resolve().parents[1] / 'docs/experiments/2026-10-12/generation-protection-service-fault/generation-protection-fault-20261012b'


@pytest.mark.parametrize('change', ['none', 'harness_stop', 'pin_count', 'ended'])
def test_fault_evidence_cannot_hide_live_process_or_ownership(tmp_path, change):
    out = tmp_path / 'evidence'
    shutil.copytree(SOURCE, out)
    cell = out / 'after-commit'
    if change == 'harness_stop':
        p = cell / 'before-harness-stop.json'
        r = json.loads(p.read_text()); r['api_returncode'] = None
        p.write_text(json.dumps(r))
    elif change in ('pin_count', 'ended'):
        p = cell / 'generation.json'
        r = json.loads(p.read_text())
        if change == 'pin_count':
            r['protected_inflight_blocks'] = 0
        else:
            for g in r['generations']:
                g['end_state'] = 'submitted'
        p.write_text(json.dumps(r))
    if change == 'none':
        assert audit(out)['passed']
    else:
        with pytest.raises(AssertionError):
            audit(out)
