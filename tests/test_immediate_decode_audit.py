from pathlib import Path

from scripts.audit_immediate_decode import audit

EVIDENCE = Path(__file__).resolve().parents[1] / 'docs/experiments/2026-10-12/generation-matrix-evidence'


def test_read_only_failure_localizes_end_then_store_without_claiming_resource_success():
    result = audit(EVIDENCE / 'immediate-decode-diagnostic-20261012a/owned-immediate-r0')
    assert not result['original_resource_gate'] and not result['exact_server_empty']
    assert len(result['residual_timelines']) == 2
    assert result['residual_end_then_store_resolve']
    assert len(result['late_store_after_end']) == 2
    assert result['reservation_closed'] and result['original_native_transfer_closed']


def test_candidate_original_receipts_close_exact_sessions_and_transfers():
    result = audit(EVIDENCE / 'immediate-decode-candidate-20261012a/owned-immediate-r0')
    assert result['original_resource_gate'] and result['exact_server_empty']
    assert result['late_store_after_end'] == result['residual_timelines'] == []
    assert result['reservation_closed'] and result['original_native_transfer_closed']
    assert result['reservation_count'] == 144 and result['native_transfer_count'] == 101
