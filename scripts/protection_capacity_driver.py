"""Generate concurrent physical demand before a pending prefix is protected."""
import json
import socket
from urllib.parse import urlsplit

from protection_shared_cancel_driver import read_rows
from remote_lookup_cancel_smoke import wait_for
from validate_environment import write_json


def exercise_capacity(out, events, api, model, tokens):
    url = urlsplit(api)
    port = url.port or 80
    streams, payloads = [], []
    try:
        for i in range(2):
            prompt = [3000 + i] + (tokens * 10)[:1599]
            payload = dict(model=model, prompt=prompt, max_tokens=2048,
                temperature=0, seed=42, ignore_eos=True, stream=True)
            payloads.append(payload)
            body = json.dumps(payload).encode()
            header = (f"POST /v1/completions HTTP/1.1\r\nHost: {url.hostname}:{port}\r\n"
                "Content-Type: application/json\r\nAccept: text/event-stream\r\n"
                f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n").encode()
            stream = socket.create_connection((url.hostname, port), timeout=10)
            streams.append(stream)
            stream.sendall(header + body)
        fallback = wait_for(lambda: next((r for r in read_rows(events)
            if r["event"] == "protection_fallback" and r.get("reason") == "capacity"
            and r["available_blocks"] >= r["required_blocks"]
            and r["inflight_blocks"] == 0), None), 60)
    finally:
        for stream in streams:
            stream.close()
        write_json(out / "capacity-pressure-payloads.json", payloads)
    write_json(out / "capacity-driver.json", dict(fallback=fallback,
        pressure_requests=len(payloads), stream_closed_after_fallback=True))
    return fallback
