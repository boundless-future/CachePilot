from copy import deepcopy
import pytest
from scripts.audit_bounded_kv import audit_rows


def evidence():
    handoffs = [dict(event="worker_bound", source="protection", request_id="raw", wire_id="w",
        batch_id="b", start=0, end=256, monotonic_ns=1)]
    service = [dict(event="transfer_submitted", request_id="w", sequence=1, kind="store", blocks=[[1]], monotonic_ns=3),
        dict(event="terminal_seen", request_id="w", sequence=1, kind="store", monotonic_ns=4)]
    shared = dict(token_prefix_sha256="p", start=0, end=256, block_ids=[1],
        layers=[dict(layer="l", sha256="digest", reference_present=True, bitwise_equal=True)])
    probes = [dict(phase="worker_layout", tensors={"l": dict(shape=[10, 2, 16, 8], element_bytes=2)}),
        dict(shared, phase="before_store", request_id="raw", monotonic_ns=2),
        dict(shared, phase="after_retrieve", request_id="later", monotonic_ns=5)]
    return handoffs, service, probes


def test_original_protected_source_is_required():
    assert audit_rows(*evidence())["strict_equal_layers"] == 1
    handoffs, service, probes = evidence()
    handoffs[0]["source"] = "default_manager"
    with pytest.raises(AssertionError):
        audit_rows(handoffs, service, probes)


@pytest.mark.parametrize("bad", ["intervening_store", "changed_kv", "missing_terminal", "wrong_blocks"])
def test_invalid_source_chain_never_counts(bad):
    handoffs, service, probes = deepcopy(evidence())
    if bad == "intervening_store":
        probes.append(dict(probes[1], request_id="other", monotonic_ns=4))
    elif bad == "changed_kv":
        probes[2]["layers"] = [dict(layer="l", sha256="wrong", reference_present=True, bitwise_equal=True)]
    elif bad == "missing_terminal":
        service.pop()
    else:
        service[0]["blocks"] = [[9]]
    with pytest.raises((AssertionError, KeyError)):
        audit_rows(handoffs, service, probes)
