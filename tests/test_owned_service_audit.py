import json

import pytest

from scripts.audit_owned_service import audit


def evidence(tmp_path, *, release_time=40, missing_terminal=False, duplicate_release=False):
    events = tmp_path / "events"
    events.mkdir()
    rows = [dict(event="lookup_registered", request_id="original", monotonic_ns=1),
            dict(event="reservation_acquired", request_id="original", lock_id=1,
                 epoch=1, serial=1, monotonic_ns=10),
            dict(event="transfer_submitted", request_id="original", sequence=1,
                 kind="retrieve", read_pins=1, monotonic_ns=20)]
    if not missing_terminal:
        rows.append(dict(event="terminal_seen", request_id="original", sequence=1,
                         kind="retrieve", monotonic_ns=30))
    release = dict(event="reservation_released", request_id="original", lock_id=1,
                   epoch=1, serial=1, disposition="RELEASED", monotonic_ns=release_time)
    rows.append(release)
    if duplicate_release:
        rows.append(release)
    rows.extend([dict(event="transfer_retired", request_id="original", sequence=1,
                      kind="retrieve", monotonic_ns=50),
                 dict(event="lookup_reclaimed", request_id="original", monotonic_ns=60),
                 dict(event="owned_shutdown_drained", monotonic_ns=70)])
    rows.sort(key=lambda r: r["monotonic_ns"])
    (events / "owned-service-1.jsonl").write_text("\n".join(map(json.dumps, rows)))
    scheduler = dict(event="scheduler_snapshot", free_blocks=909, registered_ids=[],
                     tracked_refs={}, deferred_frees=0)
    (events / "owned-connector-2.jsonl").write_text(json.dumps(scheduler) + "\n")
    return tmp_path


def test_real_token_and_terminal_audit_accepts_closed_chain(tmp_path):
    assert audit(evidence(tmp_path))["passed"]


@pytest.mark.parametrize("options", [dict(release_time=25), dict(missing_terminal=True),
                                    dict(duplicate_release=True)])
def test_final_free_blocks_do_not_hide_bad_ownership(tmp_path, options):
    with pytest.raises(AssertionError):
        audit(evidence(tmp_path, **options))
