import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from run_whole_step_efficiency import configuration, labels_for, order
from run_whole_step_identity_diagnostic import NATIVE_DEADLINE_SECONDS, configuration as diagnostic_config


def test_native_deadline_changes_only_the_existing_lmcache_deadline():
    zero = configuration("zero")
    candidate = configuration("native-deadline")
    assert labels_for("native-deadline") == ("zero", "native-deadline")
    assert candidate["kv_connector_extra_config"]["cachepilot.protection.max_chunks"] == 0
    assert candidate["kv_connector_extra_config"][
        "lmcache.mp.lazy_offload_max_deferral_seconds"] == NATIVE_DEADLINE_SECONDS
    candidate["kv_connector_extra_config"].pop("lmcache.mp.lazy_offload_max_deferral_seconds")
    zero_extra = zero["kv_connector_extra_config"]
    candidate_extra = candidate["kv_connector_extra_config"]
    zero_extra.pop("lmcache.mp.lazy_offload_max_deferral_seconds")
    assert candidate_extra == zero_extra
    assert diagnostic_config("zero")["kv_connector_extra_config"][
        "lmcache.mp.lazy_offload_max_deferral_seconds"] == 0.0


def test_native_deadline_repeats_are_counterbalanced():
    assert order(0, "native-deadline") == ["zero", "native-deadline"]
    assert order(1, "native-deadline") == ["native-deadline", "zero"]
    assert order(2, "native-deadline") == ["zero", "native-deadline"]


def test_existing_efficiency_comparison_order_is_unchanged():
    assert labels_for("eligibility") == ("zero", "legacy-two", "filtered-two")
    assert order(0) == ["zero", "legacy-two", "filtered-two"]
    assert order(1) == ["legacy-two", "filtered-two", "zero"]
