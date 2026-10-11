"""Hold a real protected STORE receipt until another candidate hits the budget."""
from protection_shared_cancel_driver import read_rows
from remote_lookup_cancel_smoke import wait_for
from validate_environment import write_json


class InflightBudgetDriver:
    def __init__(self, out, events):
        self.out, self.events, self.done, self.target = out, events, False, None

    def maybe_cancel(self):
        if self.done:
            return
        rows = read_rows(self.events)
        targets = [r for r in rows if r["event"] == "shared_target"]
        if not targets:
            return
        assert len(targets) == 1
        self.target = target = targets[0]
        fallbacks = [r for r in rows if r["event"] == "protection_fallback"
            and r.get("reason") == "inflight_budget"
            and target["request_id"] in r["inflight_request_ids"]]
        if not fallbacks:
            return
        wait_for(lambda: any(r["event"] == "shared_actual_ready"
            and r.get("batch_id") == target["batch_id"] for r in read_rows(self.events)), 15)
        (self.out / "release-shared-receipt").touch(exist_ok=False)
        write_json(self.out / "inflight-budget-driver.json", dict(target=target,
            fallback=fallbacks[0], gate_opened_after_budget_rejection=True))
        self.done = True
