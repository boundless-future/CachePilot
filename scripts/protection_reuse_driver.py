"""Issue a second real completion using the predecessor's API request ID."""
import requests

from protection_shared_cancel_driver import read_rows
from remote_lookup_cancel_smoke import wait_for
from validate_environment import write_json


class ReuseDriver:
    def __init__(self, out, events, api, model, tokens):
        self.out, self.events, self.api, self.model = out, events, api, model
        self.tokens, self.done, self.target = tokens, False, None

    def maybe_cancel(self):
        if self.done:
            return
        targets = [r for r in read_rows(self.events) if r["event"] == "reuse_target"]
        if not targets:
            return
        assert len(targets) == 1
        self.target = target = targets[0]
        rid = target["request_id"]
        assert rid.startswith("cmpl-") and rid.endswith("-0")
        api_id = rid[5:-2]
        payload = dict(model=self.model, request_id=api_id,
            prompt=[31000] + self.tokens[1:576], max_tokens=8,
            temperature=0, seed=42, ignore_eos=True)
        response = requests.post(self.api + "/v1/completions", json=payload, timeout=30)
        response.raise_for_status()
        assert response.json()["id"] == "cmpl-" + api_id
        finished = wait_for(lambda: [r for r in read_rows(self.events)
            if r["event"] == "reuse_new_finished"], 10)
        assert len(finished) == 1
        assert finished[0]["state"]["orphaned"]
        assert finished[0]["state"]["pending"]
        wait_for(lambda: [r for r in read_rows(self.events)
            if r["event"] == "shared_actual_ready"], 10)
        (self.out / "release-shared-receipt").touch(exist_ok=False)
        write_json(self.out / "reuse-driver.json", dict(target=target, payload=payload,
            response=response.json(), finished=finished, actual_request_id_reuse=True,
            vllm_request_id_randomization_disabled=True))
        self.done = True
