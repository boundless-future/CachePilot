"""Release the real receipt only after observing rejected stale envelope."""
from protection_shared_cancel_driver import read_rows
from remote_lookup_cancel_smoke import wait_for
from validate_environment import write_json


class ReceiptIdentityDriver:
    def __init__(self, out, events):
        self.out, self.events, self.done, self.target = out, events, False, None

    def maybe_cancel(self):
        if self.done:
            return
        targets = [r for r in read_rows(self.events) if r["event"] == "shared_target"]
        if not targets:
            return
        assert len(targets) == 1
        self.target = target = targets[0]

        def named(event):
            return [r for r in read_rows(self.events)
                    if r["event"] == event and r.get("batch_id") == target["batch_id"]]

        rejected = wait_for(lambda: named("receipt_identity_rejected"), 10)
        assert len(rejected) == 1 and rejected[0]["case"] == "synthetic_stale_batch"
        wait_for(lambda: named("shared_actual_ready"), 10)
        (self.out / "release-shared-receipt").touch(exist_ok=False)
        write_json(self.out / "receipt-identity-driver.json", dict(target=target,
            rejected_before_gate=rejected, actual_request_id_reuse=False))
        self.done = True
