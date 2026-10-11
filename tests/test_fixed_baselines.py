import copy
import json

import pytest

from scripts.run_fixed_baselines import (CASES, FIXED_CASES, configure,
    select_fixed, token_gate, validate_completed)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_tuning_changes_only_declared_policy_fields():
    original = dict(kv_role="kv_both", kv_load_failure_policy="recompute",
                    kv_connector_extra_config={"lmcache.mp.port": 5555})
    snapshot = copy.deepcopy(original)
    configs = [configure(original, case) for case in CASES.values()]
    assert original == snapshot
    common = []
    for config in configs:
        assert config["kv_load_failure_policy"] == "recompute"
        extra = config["kv_connector_extra_config"]
        assert extra["cachepilot.idle_finalize_grace_seconds"] == 15.0
        assert extra["lmcache.mp.lazy_offload_max_deferral_seconds"] == 0
        for field in ("lmcache.mp.lazy_offload_horizon_steps",
                      "lmcache.mp.lazy_offload_max_drain_per_step",
                      "cachepilot.protection.max_chunks"):
            extra.pop(field)
        common.append(config)
    assert all(c == common[0] for c in common)


def test_formal_rejects_failed_missing_or_changed_prerequisite(tmp_path):
    signature = {"source": "frozen", "trace": "development"}
    write(tmp_path / "plan.json", dict(stage="pilot", signature=signature, cases=CASES, repeats=1))
    for name, case in CASES.items():
        write(tmp_path / f"{name}-r0/result.json", dict(passed=True))
        write(tmp_path / f"{name}-r0/fixed-validation.json",
              dict(passed=True, signature=signature, case=case))
    assert len(validate_completed(tmp_path, signature, "pilot", CASES, 1)) == 8
    with pytest.raises(ValueError):
        validate_completed(tmp_path, {"source": "changed"}, "pilot", CASES, 1)
    target = tmp_path / "protect-two-r0/fixed-validation.json"
    invalid = json.loads(target.read_text()); invalid["passed"] = False
    write(target, invalid)
    with pytest.raises(ValueError):
        validate_completed(tmp_path, signature, "pilot", CASES, 1)
    target.unlink()
    with pytest.raises(FileNotFoundError):
        validate_completed(tmp_path, signature, "pilot", CASES, 1)


def test_selection_cannot_choose_protection_or_missing_repeat():
    rows = [(name, repeat, {"ttft_ms": {"p95": 20 + index}})
            for index, name in enumerate(FIXED_CASES) for repeat in range(3)]
    rows += [("protect-one", repeat, {"ttft_ms": {"p95": 1}}) for repeat in range(3)]
    assert select_fixed(rows)["selected"] == next(iter(FIXED_CASES))
    with pytest.raises(ValueError):
        select_fixed(rows[1:])


def test_token_gate_rejects_duplicate_or_missing_events(tmp_path):
    trace = {"events": [{"event_id": n, "max_tokens": 48} for n in (1, 2)]}
    rows = [dict(event_id=n, error=None, usage={"completion_tokens": 48}) for n in (1, 2)]
    write(tmp_path / "requests.json", {"results": rows})
    assert token_gate(tmp_path, trace)
    write(tmp_path / "requests.json", {"results": [rows[0], rows[0]]})
    assert not token_gate(tmp_path, trace)
    rows[1]["usage"]["completion_tokens"] = 47
    write(tmp_path / "requests.json", {"results": rows})
    assert not token_gate(tmp_path, trace)
