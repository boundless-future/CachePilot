"""Installed metadata contracts without starting a server or touching CUDA."""
from copy import deepcopy
import pickle
from types import SimpleNamespace as NS
import pytest

pytest.importorskip("lmcache")
from cachepilot.integration.vllm.owned_service import OwnedServiceConnector
from cachepilot.integration.lmcache.protocol import CLIENT_CONFIG, SEQUENCE_CONFIG, GENERATION_CONFIG
from cachepilot.integration.vllm.protection import HandoffObservedConnector, ProtectionConnector, HandoffWorkerMetadata
from cachepilot.runtime.handoff import HandoffLedger, ProtectionConfig
from lmcache.integration.vllm.lmcache_mp_metadata import LMCacheMPWorkerMetadata


def connector(cls=ProtectionConnector):
    obj = object.__new__(cls)
    obj.rows = []
    obj._handoff_record = lambda event, **kw: obj.rows.append(dict(event=event, **kw))
    obj._handoffs = HandoffLedger(obj._handoff_record)
    obj._protection_config = ProtectionConfig(True, 0)
    obj._handoff_serial = 0
    obj._bound_handoffs = []
    return obj


def metadata():
    return NS(requests=[NS(request_id="r", direction="STORE", cache_salt="",
        request_configs={CLIENT_CONFIG: 1, SEQUENCE_CONFIG: 1, GENERATION_CONFIG: "a"*32},
        op=NS(start=0, end=256, skip_first_n_tokens=0, token_ids=list(range(256)),
              block_ids=[list(range(16))]))])


def test_real_metadata_receipt_roundtrip_and_duplicate_rejection(monkeypatch):
    source = metadata()
    snapshot = deepcopy(source.requests)
    monkeypatch.setattr(OwnedServiceConnector, "build_connector_meta", lambda *a: source)
    monkeypatch.setattr(OwnedServiceConnector, "bind_connector_metadata", lambda *a: None)
    monkeypatch.setattr(OwnedServiceConnector, "wait_for_save", lambda *a: None)
    receipt = LMCacheMPWorkerMetadata({"r": 1}, {"r"})
    monkeypatch.setattr(OwnedServiceConnector, "build_connector_worker_meta", lambda *a: receipt)
    seen = []
    monkeypatch.setattr(OwnedServiceConnector, "update_connector_output", lambda self, output: seen.append(output))
    scheduler, worker = connector(), connector()
    built = scheduler.build_connector_meta(NS(total_num_scheduled_tokens=1))
    assert built is source and source.requests == snapshot
    worker.bind_connector_metadata(pickle.loads(pickle.dumps(built)))
    worker.wait_for_save()
    result = pickle.loads(pickle.dumps(worker.build_connector_worker_meta()))
    assert isinstance(result, HandoffWorkerMetadata) and result.failed_store_requests == {"r"}
    scheduler.update_connector_output(NS(kv_connector_worker_meta=result))
    assert len(seen) == 1 and not scheduler._handoffs.pending and not worker._handoffs.pending
    with pytest.raises(ValueError):
        scheduler.update_connector_output(NS(kv_connector_worker_meta=result))
    assert len(seen) == 1


def test_corrupt_or_missing_handoff_rejected_before_worker_binding(monkeypatch):
    source = metadata()
    monkeypatch.setattr(OwnedServiceConnector, "build_connector_meta", lambda *a: source)
    def forbidden(*args):
        pytest.fail("Invalid handoff reached upstream")
    monkeypatch.setattr(OwnedServiceConnector, "bind_connector_metadata", forbidden)
    scheduler, worker = connector(), connector()
    with pytest.raises(ValueError):
        worker.bind_connector_metadata(source)
    scheduler.build_connector_meta(NS(total_num_scheduled_tokens=1))
    source.requests[0].op.block_ids[0][0] = 99
    with pytest.raises(ValueError):
        worker.bind_connector_metadata(source)
    assert not worker._handoffs.pending


def test_zero_budget_cannot_mutate_default_operation(monkeypatch):
    source = metadata()
    monkeypatch.setattr(OwnedServiceConnector, "build_connector_meta", lambda *a: source)
    obj = connector()
    def corrupt(meta, output):
        meta.requests[0].op.end = 512
        return meta
    obj._apply_protection = corrupt
    with pytest.raises(RuntimeError, match="changed the default"):
        obj.build_connector_meta(NS(total_num_scheduled_tokens=1))
    assert not obj._handoffs.pending


def test_summary_counts_total_concurrent_pins_without_hot_path_files(monkeypatch, tmp_path):
    import json
    monkeypatch.delenv("CACHEPILOT_HANDOFF_DIR", raising=False)
    monkeypatch.setenv("CACHEPILOT_PROTECTION_SUMMARY_PATH", str(tmp_path))
    obj = object.__new__(ProtectionConnector)
    obj._protection_summary = None
    obj._handoff_role = "SCHEDULER"
    obj._handoffs = NS(pending={})
    obj._bounded = NS(prepared={"a": NS(blocks=list(range(16)))},
        inflight={"b": NS(blocks=list(range(16)))})
    obj._handoff_record("protection_prepared", acquired_pins=16)
    assert obj._protection_summary["peak_pinned_blocks"] == 32
    assert obj._protection_summary["peak_inflight_blocks"] == 16
    assert not list(tmp_path.iterdir())
    obj._bounded.prepared.clear()
    obj._bounded.inflight.clear()
    monkeypatch.setattr(OwnedServiceConnector, "shutdown", lambda self: None)
    obj.shutdown()
    state = json.loads(next(tmp_path.glob("*.json")).read_text())
    assert state["shutdown_complete"] and state["inflight_blocks_before_shutdown"] == 0
