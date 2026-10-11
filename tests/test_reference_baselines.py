import copy
from pathlib import Path

import pytest

from scripts.run_reference_baselines import cases_for, immediate_config, reference_logs, resource_gate


@pytest.fixture(autouse=True)
def script_imports(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))


def test_case_selection_keeps_native_and_immediate_without_double_default():
    default = cases_for("h2p5-d64")
    assert list(default) == ["native", "owned-immediate", "h2p5-d64", "protect-one", "protect-two"]
    other = cases_for("h1p25-d1")
    assert len(other) == 6 and "h2p5-d64" in other and "h1p25-d1" in other
    with pytest.raises(ValueError):
        cases_for("protect-one")


def test_immediate_preserves_owned_identity_path_without_mutating_input():
    original = dict(kv_connector="OwnedServiceConnector", kv_load_failure_policy="recompute",
                    kv_connector_extra_config={"lmcache.mp.port": 5555})
    before = copy.deepcopy(original)
    actual = immediate_config(original)
    assert original == before
    assert actual["kv_connector"] == "OwnedServiceConnector"
    assert actual["kv_load_failure_policy"] == "recompute"
    assert actual["kv_connector_extra_config"]["lmcache.mp.lazy_offload"] is False
    with pytest.raises(ValueError):
        immediate_config(dict(kv_connector="LMCacheMPConnector"))


def test_native_requires_clean_engine_exit_and_no_mp_service():
    assert resource_gate("native", dict(engine_returncode=0, mp_service_started=False))
    assert not resource_gate("native", dict(engine_returncode=-9, mp_service_started=False))
    assert not resource_gate("native", dict(engine_returncode=0, mp_service_started=True))
    assert not resource_gate("native", {})
    # An ordinary exit is insufficient evidence for a caching configuration.
    assert not resource_gate("immediate", dict(engine_returncode=0, mp_service_started=False))


def test_native_error_logs_cannot_be_hidden_as_missing_cache_service(tmp_path):
    assert reference_logs(tmp_path, None, True)["fatal_errors"]
    (tmp_path / "vllm.log").write_text("INFO ready\nERROR runtime failure\n")
    report = reference_logs(tmp_path, None, True)
    assert len(report["fatal_errors"]) == 1 and not report["shutdown_teardown_errors"]
    (tmp_path / "vllm.log").write_text("INFO normal exit\n")
    assert reference_logs(tmp_path, None, True)["fatal_errors"] == []
    # The same absence of an MP log is a failure for a cache-backed run.
    assert reference_logs(tmp_path, None, False)["fatal_errors"]
