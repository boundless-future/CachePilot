"""Driver hook: cancel a raw streaming request before opening the receipt gate."""
import json
import socket
from urllib.parse import urlsplit

from remote_lookup_cancel_smoke import wait_for
from validate_environment import write_json


def read_rows(directory):
    rows = []
    for path in directory.glob("handoff-*.jsonl"):
        for line in path.read_text().splitlines():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass  # The writer may still be appending the last line.
    return rows


class SharedCancelDriver:
    def __init__(self, out, events, api, model, suffix):
        self.out, self.events, self.api, self.model = out, events, api, model
        self.suffix, self.target, self.done = suffix, None, False

    def maybe_cancel(self):
        if self.done:
            return
        rows = read_rows(self.events)
        targets = [r for r in rows if r["event"] == "shared_target"]
        if not targets:
            return
        assert len(targets) == 1
        self.target = target = targets[0]
        bid = target["batch_id"]

        def event(name):
            return next((r for r in read_rows(self.events)
                         if r["event"] == name and r.get("batch_id") == bid), None)

        wait_for(lambda: event("shared_gate_installed"), 10)
        body = json.dumps(dict(model=self.model, prompt=target["token_ids"] + self.suffix,
            max_tokens=2048, temperature=0, seed=42, ignore_eos=True, stream=True)).encode()
        url = urlsplit(self.api)
        port = url.port or 80
        header = (f"POST /v1/completions HTTP/1.1\r\nHost: {url.hostname}:{port}\r\n"
            "Content-Type: application/json\r\nAccept: text/event-stream\r\n"
            f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n").encode()
        with socket.create_connection((url.hostname, port), timeout=10) as stream:
            stream.sendall(header + body)
            live = wait_for(lambda: event("shared_live_refs"), 15)
            assert live["request_id"] != target["request_id"]
            assert set(live["shared_ids"]) == set(target["block_ids"][0])
            assert all(v == 2 for v in live["refs"].values())
            # Closing the client exercises vLLM's normal cancellation path.
        cancelled = wait_for(lambda: event("shared_free_after"), 15)
        assert cancelled["request_id"] == live["request_id"]
        assert cancelled["status"] == "FINISHED_ABORTED"
        assert all(v == 1 for v in cancelled["refs"].values())
        assert cancelled["protection_inflight"] and cancelled["manager_inflight"]
        wait_for(lambda: event("shared_actual_ready"), 15)
        (self.out / "release-shared-receipt").touch(exist_ok=False)
        write_json(self.out / "shared-cancel-driver.json", dict(target=target,
            shared_request_id=live["request_id"], cancelled=cancelled,
            payload=json.loads(body), gate_opened_after_cancel=True))
        self.done = True
