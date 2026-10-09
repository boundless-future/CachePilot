"""Require real over-budget candidates and unchanged default STORE ownership."""
def audit_rows(rows):
    fallbacks = [r for r in rows if r["event"] == "protection_fallback"
                 and r["reason"] == "candidate_exceeds_budget"]
    assert fallbacks, "No actual over-budget candidate observed"
    assert not any(r["event"] in ("protection_prepared", "protection_metadata_committed") for r in rows)
    for row in fallbacks:
        assert row["required_blocks"] == 32 and row["available_blocks"] == row["budget_blocks"] == 16
        assert row["acquired_pins"] == 0 and row["start"] == 0 and row["end"] == 512
    default = [r for r in rows if r["event"] == "scheduler_built"]
    assert default and all(r["source"] == "default_manager" for r in default)
    # At least one declined operation must later traverse the native manager.
    key = lambda r: (r["wire_id"], r["prefix_sha256"], r["start"], r["end"])
    identities = {key(r) for r in fallbacks}
    saved = [r for r in default if any(key(f) == key(r)
             and f["monotonic_ns"] < r["monotonic_ns"] for f in fallbacks)]
    assert saved, "No declined operation later saved by the default manager"
    return dict(passed=True, declined_observations=len(fallbacks),
                declined_operations=len(identities), default_saved_operations=len(saved),
                acquired_protection_pins=0, added_protection_stores=0)
