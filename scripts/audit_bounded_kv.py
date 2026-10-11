"""Link protected STORE -> actual service transfer -> later unchanged KV.

This strict subset excludes intervening STORE attempts, even if an attempt
only finds an already-saved object. It reports coverage rather than treating
every retrieved chunk as proof of the protection path.
"""
import argparse
import json
import math
from pathlib import Path


def read_rows(paths):
    return [json.loads(line) for path in paths for line in path.read_text(encoding="utf-8").splitlines()]


def audit_rows(handoffs, service, probes):
    protected = [r for r in handoffs if r["event"] == "worker_bound" and r["source"] == "protection"]
    submits = [r for r in service if r["event"] == "transfer_submitted"]
    terminal = {(r["request_id"], r["sequence"], r["kind"]): r for r in service if r["event"] == "terminal_seen"}
    stores = [r for r in probes if r["phase"] == "before_store"]
    strict = []
    for load in (r for r in probes if r["phase"] == "after_retrieve"):
        history = [r for r in stores if r["token_prefix_sha256"] == load["token_prefix_sha256"]
                   and r["monotonic_ns"] < load["monotonic_ns"]]
        if not history:
            continue
        original = max(history, key=lambda r: r["monotonic_ns"])
        # A real raw request ID may have several generations. Bind this probe
        # to the most recent worker handoff (including default STOREs), never
        # to every earlier protected handoff with that same raw ID.
        matches = [r for r in handoffs if r["event"] == "worker_bound"
                   and r["request_id"] == original["request_id"]
                   and r["monotonic_ns"] < original["monotonic_ns"]]
        if not matches:
            continue
        batch = max(matches, key=lambda r: r["monotonic_ns"])
        if batch["source"] != "protection":
            continue
        assert batch["start"] <= original["start"] < original["end"] <= batch["end"]
        transfers = [r for r in submits if r["request_id"] == batch["wire_id"] and r["kind"] == "store"
            and original["monotonic_ns"] < r["monotonic_ns"] < load["monotonic_ns"]
            and set(original["block_ids"]) <= {b for g in r["blocks"] for b in g}]
        if not transfers:  # Duplicate STORE metadata need not issue a DMA.
            continue
        assert len(transfers) == 1
        transfer = transfers[0]
        done = terminal[(transfer["request_id"], transfer["sequence"], "store")]
        assert transfer["monotonic_ns"] < done["monotonic_ns"] < load["monotonic_ns"]
        before = {layer["layer"]: layer["sha256"] for layer in original["layers"]}
        after = {layer["layer"]: layer["sha256"] for layer in load["layers"]}
        assert before and before == after
        assert all(layer.get("bitwise_equal") and layer.get("reference_present") for layer in load["layers"])
        strict.append(dict(batch_id=batch["batch_id"], token_prefix_sha256=load["token_prefix_sha256"],
                           start=load["start"], end=load["end"], equal_layers=len(after),
                           store_sequence=transfer["sequence"]))
    assert strict, "No protected source with actual transfer and no intervening STORE"
    layout = next(r for r in probes if r["phase"] == "worker_layout")
    block_bytes = sum(math.prod(t["shape"][1:]) * t["element_bytes"] for t in layout["tensors"].values())
    return dict(passed=True, strict_chunks=len(strict), strict_equal_layers=sum(r["equal_layers"] for r in strict),
        covered_batches=len({r["batch_id"] for r in strict}), protected_batches=len(protected),
        kv_bytes_per_block=block_bytes,
        # Logical payload from actual native submissions, NOT DMA counters.
        submitted_payload_bytes={kind: sum(sum(map(len, r["blocks"])) * block_bytes for r in submits if r["kind"] == kind)
                                 for kind in ("store", "retrieve")}, strict_sources=strict)


def dma_checkpoint(directory):
    lines = (directory / "after-restore-lmcache.prom").read_text(encoding="utf-8").splitlines()
    return {direction: sum(float(line.rsplit(" ", 1)[1]) for line in lines
                if line.startswith("lmcache_mp_transfer_phase_bytes_total{")
                and f'direction="{direction}"' in line and 'phase="staging"' in line)
            for direction in ("h2d", "d2h")}


def audit(directory):
    result = audit_rows(read_rows((directory / "events").glob("handoff-*.jsonl")),
        read_rows((directory / "events").glob("owned-service-*.jsonl")),
        read_rows((directory / "probe").glob("kv-*.jsonl")))
    result["dma_bytes_after_first_target_restore"] = dma_checkpoint(directory)
    result["dma_checkpoint_scope"] = "Before pressure-prefix replay; not total experiment DMA"
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(json.dumps(audit(args.directory), indent=2) + "\n")
