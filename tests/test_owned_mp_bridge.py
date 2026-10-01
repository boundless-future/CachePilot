from types import SimpleNamespace

import pytest

from scripts.owned_mp_connector import GENERATION_CONFIG, OwnedMPConnector
from scripts.owned_mp_server import generation, install_guard


def key(nonce, **changes):
    fields = dict(request_id="r", model_name="model", world_size=1,
                  cache_salt="", token_ids=tuple(range(512)), start=0,
                  end=512, worker_id=None, num_kv_readers=1,
                  request_configs={GENERATION_CONFIG: nonce})
    fields.update(changes)
    return SimpleNamespace(**fields)


def test_connector_keeps_generation_for_existing_tracker(monkeypatch):
    from lmcache.integration.vllm.lmcache_mp_connector import LMCacheMPConnector

    tracker = SimpleNamespace(request_configs={GENERATION_CONFIG: "f" * 32})
    monkeypatch.setattr(LMCacheMPConnector, "_get_or_create_request_tracker",
                        lambda self, request: tracker)
    connector = object.__new__(OwnedMPConnector)
    first = connector._get_or_create_request_tracker(None)
    nonce = first.request_configs[GENERATION_CONFIG]
    assert len(nonce) == 32
    assert nonce != "f" * 32
    assert connector._get_or_create_request_tracker(None).request_configs[
        GENERATION_CONFIG] == nonce


@pytest.mark.parametrize("value", [None, "", "bad", "z" * 32, 123])
def test_generation_rejects_invalid_wire_value(value):
    with pytest.raises(ValueError):
        generation(key("0" * 32, request_configs={GENERATION_CONFIG: value}))


def test_real_handler_guard_rejects_wrong_generation_and_short_blocks(
    monkeypatch, tmp_path
):
    from lmcache.v1.multiprocess.modules.lookup import LookupModule
    from lmcache.v1.multiprocess.modules.lmcache_driven_transfer import (
        LMCacheDrivenTransferModule,
    )

    calls = []
    monkeypatch.setattr(LookupModule, "lookup",
                        lambda self, cache_key, *args: calls.append("lookup"))
    monkeypatch.setattr(LookupModule, "end_session",
                        lambda self, request_id: calls.append("end"))
    monkeypatch.setattr(LMCacheDrivenTransferModule, "retrieve",
                        lambda self, *args: (b"event", True))
    install_guard(tmp_path)
    lookup = SimpleNamespace()
    block_context = SimpleNamespace(kv_layer_groups_manager=SimpleNamespace(
        num_kernel_groups=1), calculate_num_blocks=lambda tokens, group: 16)
    transfer = SimpleNamespace(
        get_and_touch_context_entry=lambda instance: SimpleNamespace(
            cache_context=block_context),
        _ctx=SimpleNamespace(chunk_size=256),
        _release_failed_retrieve_locks=lambda cache_key, instance: calls.append(
            "released"),
    )
    nonce = "a" * 32
    LookupModule.lookup(lookup, key(nonce), 1)
    retrieve = LMCacheDrivenTransferModule.retrieve
    assert retrieve(transfer, key("b" * 32, worker_id=0), 1,
                    [[1] * 32], b"") == (b"", False)
    assert retrieve(transfer, key(nonce, worker_id=0), 1,
                    [[]], b"") == (b"", False)
    assert calls == ["lookup", "released"]
    assert retrieve(transfer, key(nonce, worker_id=0), 1,
                    [[1] * 32], b"") == (b"event", True)
    LookupModule.end_session(lookup, "r")
    assert retrieve(transfer, key(nonce, worker_id=0), 1,
                    [[1] * 32], b"") == (b"", False)
