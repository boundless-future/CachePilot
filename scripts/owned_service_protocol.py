"""Versioned identity at the real RequestClient boundary."""
from dataclasses import replace

CLIENT_CONFIG = "cachepilot.client.v1"
SEQUENCE_CONFIG = "cachepilot.sequence.v1"
GENERATION_CONFIG = "cachepilot.lookup_generation.v1"


def wire_id(key):
    configs = key.request_configs or {}
    client, sequence, nonce = (configs.get(name) for name in
                              (CLIENT_CONFIG, SEQUENCE_CONFIG, GENERATION_CONFIG))
    if (type(client) is not int or not 0 < client < 2**62
            or type(sequence) is not int or not 0 < sequence < 2**63
            or not isinstance(nonce, str) or len(nonce) != 32
            or len(bytes.fromhex(nonce)) != 16):
        raise ValueError("Invalid owned service identity")
    return f"cp1:{client}:{sequence}:{nonce}:{key.request_id}"


def parse_id(request_id):
    parts = request_id.split(":", 4)
    if len(parts) != 5 or parts[0] != "cp1":
        raise ValueError("Owned wire identity required")
    client, sequence = int(parts[1]), int(parts[2])
    if (not 0 < client < 2**62 or not 0 < sequence < 2**63
            or len(parts[3]) != 32 or len(bytes.fromhex(parts[3])) != 16):
        raise ValueError("Invalid owned wire identity")
    return client, sequence


def coalesce_owned_store_metadata(metadatas, coalesce):
    if not metadatas:
        raise ValueError("Cannot coalesce an empty owned batch")
    first = metadatas[0]
    identity = wire_id(first)
    configs = dict(first.request_configs)
    for metadata in metadatas[1:]:
        if (wire_id(metadata) != identity or metadata.cache_salt != first.cache_salt
                or metadata.request_configs != configs):
            raise ValueError("Cannot coalesce different owned STORE identities")
    # The pinned manager drops request_configs when merging multiple chunks.
    # Preserve the acquisition-time identity, never the current tracker.
    return replace(coalesce(metadatas), request_configs=configs)


class OwnedRequestClient:
    def __init__(self, client, incarnation=None):
        self.client, self.incarnation = client, incarnation
        self.identities = {}
        self.registered = False

    def __getattr__(self, name):
        return getattr(self.client, name)

    def ping(self, instance_id=None):
        if instance_id is None and self.incarnation is not None:
            instance_id = -self.incarnation
        return self.client.ping(instance_id)

    def lookup(self, key, *args, **kwargs):
        if not self.registered:
            if not self.ping().result(timeout=10):
                raise RuntimeError("Owned client incarnation rejected")
            self.registered = True
        identity = wire_id(key)
        previous = self.identities.get(key.request_id)
        if previous is not None and previous != identity:
            # A tracker can be recreated after preemption while the previous
            # lookup owner still exists. End exactly that old generation.
            self.client.end_session(previous)
        self.identities[key.request_id] = identity
        return self.client.lookup(replace(key, request_id=identity), *args, **kwargs)

    def query_prefetch_status(self, request_id):
        return self.client.query_prefetch_status(self.identities[request_id])

    def query_prefetch_lookup_hits(self, request_id):
        return self.client.query_prefetch_lookup_hits(self.identities[request_id])

    def free_lookup_locks(self, key, *args, **kwargs):
        return self.client.free_lookup_locks(replace(key, request_id=wire_id(key)), *args, **kwargs)

    def end_session(self, request_id):
        identity = self.identities.pop(request_id, None)
        if identity is not None:
            return self.client.end_session(identity)

    def store(self, key, *args, **kwargs):
        return self.client.store(replace(key, request_id=wire_id(key)), *args, **kwargs)

    def retrieve(self, key, *args, **kwargs):
        return self.client.retrieve(replace(key, request_id=wire_id(key)), *args, **kwargs)
