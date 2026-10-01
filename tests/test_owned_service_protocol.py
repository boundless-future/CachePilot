from concurrent.futures import Future
from dataclasses import dataclass, field, replace

import pytest

from scripts.owned_service_protocol import (CLIENT_CONFIG, GENERATION_CONFIG,
    SEQUENCE_CONFIG, OwnedRequestClient, coalesce_owned_store_metadata, parse_id, wire_id)


@dataclass
class Key:
    request_id: str = "reused:id"
    cache_salt: str = "salt"
    request_configs: dict = field(default_factory=lambda: {
        CLIENT_CONFIG: 7, SEQUENCE_CONFIG: 1, GENERATION_CONFIG: "a" * 32})


class Client:
    def __init__(self):
        self.calls = []

    def ping(self, instance):
        self.calls.append(("ping", instance))
        result = Future()
        result.set_result(True)
        return result

    def __getattr__(self, name):
        def call(value, *args, **kwargs):
            self.calls.append((name, getattr(value, "request_id", value)))
        return call


def test_wire_keeps_original_id_colons_and_generation():
    assert parse_id(wire_id(Key())) == (7, 1)
    assert wire_id(Key()).endswith(":reused:id")


@pytest.mark.parametrize("name,value", [(CLIENT_CONFIG, True), (CLIENT_CONFIG, 0),
    (CLIENT_CONFIG, 2**62), (SEQUENCE_CONFIG, True), (SEQUENCE_CONFIG, 2**63),
    (GENERATION_CONFIG, "z" * 32), (GENERATION_CONFIG, "a" * 30)])
def test_bad_wire_identity_rejected_before_rpc(name, value):
    key = Key()
    key.request_configs[name] = value
    with pytest.raises(ValueError):
        wire_id(key)


def test_tracker_replacement_ends_exact_previous_generation():
    client = Client()
    proxy = OwnedRequestClient(client, 7)
    old = Key()
    proxy.lookup(old)
    new = Key(request_configs={CLIENT_CONFIG: 7, SEQUENCE_CONFIG: 2,
                              GENERATION_CONFIG: "b" * 32})
    proxy.lookup(new)
    proxy.query_prefetch_status(new.request_id)
    proxy.end_session(new.request_id)
    proxy.end_session(new.request_id)
    assert client.calls == [("ping", -7), ("lookup", wire_id(old)),
        ("end_session", wire_id(old)), ("lookup", wire_id(new)),
        ("query_prefetch_status", wire_id(new)), ("end_session", wire_id(new))]


def test_merged_store_keeps_acquisition_identity_after_tracker_changes():
    old = Key()
    second = replace(old, request_configs=dict(old.request_configs))
    merged = coalesce_owned_store_metadata([old, second],
        lambda metas: replace(metas[0], request_configs=None))
    old.request_configs[GENERATION_CONFIG] = "b" * 32
    assert wire_id(merged) == wire_id(second)
    assert merged.request_configs is not second.request_configs


@pytest.mark.parametrize("change", ["generation", "sequence", "raw_id", "salt", "config"])
def test_merged_store_rejects_identity_changes_before_coalescing(change):
    old, new = Key(), Key()
    if change == "generation":
        new.request_configs[GENERATION_CONFIG] = "b" * 32
    elif change == "sequence":
        new.request_configs[SEQUENCE_CONFIG] = 2
    elif change == "raw_id":
        new.request_id = "other"
    elif change == "salt":
        new.cache_salt = "other"
    else:
        new.request_configs["other"] = 1
    def never_called(metas):
        pytest.fail("Invalid identity reached native coalescer")
    with pytest.raises(ValueError, match="different owned"):
        coalesce_owned_store_metadata([old, new], never_called)
