"""Lightweight identity and RPC doubles, with no upstream imports."""
from concurrent.futures import Future
from dataclasses import dataclass, field
from cachepilot.integration.lmcache.protocol import CLIENT_CONFIG, SEQUENCE_CONFIG, GENERATION_CONFIG

@dataclass
class Key:
    request_id: str = "reused:id"
    cache_salt: str = "salt"
    request_configs: dict = field(default_factory=lambda: {
        CLIENT_CONFIG: 7, SEQUENCE_CONFIG: 1, GENERATION_CONFIG: "a" * 32})


class Client:
    def __init__(self):
        self.calls = []

    def ping(self, instance):
        self.calls.append(("ping", instance))
        result = Future()
        result.set_result(True)
        return result

    def __getattr__(self, name):
        def call(value, *args, **kwargs):
            self.calls.append((name, getattr(value, "request_id", value)))
        return call


class Transport:
    def __init__(self):
        self.calls, self.futures = [], []

    def ping(self, value):
        self.calls.append(('ping', value))
        future = Future()
        future.set_result(True)
        return future

    def __getattr__(self, name):
        def send(key, *args, **kwargs):
            self.calls.append((name, getattr(key, 'request_id', key)))
            future = Future()
            self.futures.append(future)
            return future
        return send
