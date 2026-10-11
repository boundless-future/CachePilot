"""Capacity pressure from four real streams; stop them after natural preemption."""
import json
import socket
from urllib.parse import urlsplit

from protection_shared_cancel_driver import read_rows
from remote_lookup_cancel_smoke import wait_for
from validate_environment import write_json


class ProtectedPreemptionDriver:
    def __init__(self, out, events, api, model, tokens, shared_victim=False):
        self.out, self.events, self.api, self.model = out, events, api, model
        self.tokens, self.target, self.done = tokens, None, False
        self.shared_victim = shared_victim

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

        wait_for(lambda: named("shared_gate_installed"), 10)
        url = urlsplit(self.api)
        port = url.port or 80
        streams, payloads = [], []
        try:
            for i in range(4):
                # Distinct first physical blocks, no sharing with the protected
                # source. 4 x 3500 prefill plus decode exceeds the 909-block pool.
                prompt = ([1000 + i] + (self.tokens * 20)[:3499]
                    if not self.shared_victim or i < 3 else
                    target["token_ids"] + (self.tokens * 20)[:3500-len(target["token_ids"])])
                assert len(prompt) == 3500
                assert (prompt[:16] == target["token_ids"][:16]) == (self.shared_victim and i == 3)
                payload = dict(model=self.model, prompt=prompt, max_tokens=2048,
                    temperature=0, seed=42, ignore_eos=True, stream=True)
                payloads.append(payload)
                body = json.dumps(payload).encode()
                header = (f"POST /v1/completions HTTP/1.1\r\nHost: {url.hostname}:{port}\r\n"
                    "Content-Type: application/json\r\nAccept: text/event-stream\r\n"
                    f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n").encode()
                stream = socket.create_connection((url.hostname, port), timeout=10)
                streams.append(stream)
                stream.sendall(header + body)
            if self.shared_victim:
                preemptions = wait_for(lambda: [r for r in named("protected_preempt_before")
                    if set(r["victim_blocks"]) >= set(target["block_ids"][0])], 75)
            else:
                preemptions = wait_for(lambda: named("protected_preempt_after"), 75)
        finally:
            for stream in streams:
                stream.close()
            write_json(self.out / "protected-pressure-payloads.json", payloads)
        arrived = named("protected_pressure_arrived")
        ids = {r["request_id"] for r in arrived if r["prompt_tokens"] == 3500 and
            (r["first_token"] in range(1000, 1004) or
             (self.shared_victim and r["first_token"] == target["token_ids"][0]))}
        assert len(ids) == 4
        finished = wait_for(lambda: (rows if {r["request_id"] for r in rows} >= ids else None)
            if (rows := named("protected_pressure_finished")) else None, 15)
        assert all(r["status"] == "FINISHED_ABORTED" for r in finished if r["request_id"] in ids)
        wait_for(lambda: named("shared_actual_ready"), 10)
        (self.out / "release-shared-receipt").touch(exist_ok=False)
        write_json(self.out / "protected-preemption-driver.json", dict(target=target,
            pressure_request_ids=sorted(ids), preemptions=preemptions, finished=finished,
            gate_opened_after_pressure_cancel=True, shared_prefix_victim_required=self.shared_victim))
        self.done = True
