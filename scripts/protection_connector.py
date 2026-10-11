"""Single-worker protection integration on the owned MP service.

The native manager owns committed STORE pins and processes original receipts.
Diagnostic envelopes carry batch identity across scheduler/worker boundaries.
Nonzero protection uses the separate, bounded scheduler-owner adapter.
"""
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import time

from lmcache.integration.vllm.lmcache_mp_metadata import LMCacheMPWorkerMetadata
from scripts.owned_service_connector import OwnedServiceConnector
from scripts.protection_handoff import HandoffLedger, ProtectionConfig, describe


@dataclass
class HandoffWorkerMetadata(LMCacheMPWorkerMetadata):
    batch_ids: dict[str, str] = field(default_factory=dict)

    def aggregate(self, other):
        # The runtime is restricted to one worker; fail closed on accidental
        # cross-rank aggregation rather than lose the original batch IDs.
        raise ValueError("Protection handoff supports TP=PP=DP=1 only")


class HandoffObservedConnector(OwnedServiceConnector):
    """Common diagnostic envelope for the baseline and candidate."""
    def __init__(self, vllm_config, role, kv_cache_config=None):
        parallel = vllm_config.parallel_config
        if (parallel.world_size != 1 or parallel.data_parallel_size != 1
                or parallel.decode_context_parallel_size != 1):
            raise ValueError("Protection handoff supports a single worker only")
        extra = vllm_config.kv_transfer_config.kv_connector_extra_config
        if not extra.get("lmcache.mp.lazy_offload", False) or extra.get(
                "lmcache.mp.lazy_offload_policy") != "EVICTION_AWARE":
            raise ValueError("Zero-budget comparison requires EVICTION_AWARE")
        self._protection_config = ProtectionConfig.parse(extra)
        self._handoff_role = role.name
        self._handoff_serial = 0
        self._handoffs = HandoffLedger(self._handoff_record)
        self._bound_handoffs = []
        self._bounded = None
        self._protection_summary = None
        super().__init__(vllm_config, role, kv_cache_config)
        if self._group_tokens_per_block != [16]:
            raise ValueError("Protection handoff requires one 16-token dense group")
        if self._protection_config.enabled and self._protection_config.max_chunks:
            if type(self)._apply_protection is HandoffObservedConnector._apply_protection:
                raise ValueError("Nonzero budget requires ProtectionConnector")
            if (vllm_config.scheduler_config.async_scheduling
                    or vllm_config.speculative_config is not None
                    or vllm_config.lora_config is not None):
                raise ValueError("Bounded protection requires synchronous text-only scheduling without speculation/LoRA")
            if role.name == "SCHEDULER":
                from scripts.bounded_protection import BoundedProtection
                self._bounded = BoundedProtection(self, vllm_config)
        self._handoff_record("initialized", connector=type(self).__name__,
            enabled=self._protection_config.enabled, max_chunks=self._protection_config.max_chunks)

    def _handoff_record(self, event, **fields):
        directory = os.environ.get("CACHEPILOT_HANDOFF_DIR")
        if directory:
            path = Path(directory)
            path.mkdir(parents=True, exist_ok=True)
            with (path / f"handoff-{os.getpid()}-{self._handoff_role}.jsonl").open("a") as f:
                f.write(json.dumps(dict(event=event, pid=os.getpid(), role=self._handoff_role,
                    monotonic_ns=time.monotonic_ns(), **fields), sort_keys=True) + "\n")
        summary = os.environ.get("CACHEPILOT_PROTECTION_SUMMARY_PATH")
        if summary:
            # Keep measurement-path writes off the scheduler hot path. Each
            # process flushes its aggregate once during normal connector close.
            if self._protection_summary is None:
                self._protection_summary = dict(pid=os.getpid(), role=self._handoff_role, events={},
                    fallbacks={}, prepared_batches=0, committed_batches=0,
                    receipts=0, failed_receipts=0, peak_pinned_blocks=0,
                    peak_inflight_blocks=0)
            state = self._protection_summary
            state["events"][event] = state["events"].get(event, 0) + 1
            if event == "protection_fallback":
                reason = fields.get("reason", "unknown")
                state["fallbacks"][reason] = state["fallbacks"].get(reason, 0) + 1
            if event == "protection_prepared":
                state["prepared_batches"] += 1
            if event == "protection_metadata_committed":
                state["committed_batches"] += 1
            if event == "protection_receipt":
                state["receipts"] += 1
                state["failed_receipts"] += int(fields.get("failed", False))
            if event == "protection_prepared":
                state["peak_pinned_blocks"] = max(state["peak_pinned_blocks"],
                    int(fields.get("acquired_pins", 0)))
            if event == "protection_metadata_committed":
                state["peak_inflight_blocks"] = max(state["peak_inflight_blocks"],
                    int(fields.get("owned_pins", 0)))

    def shutdown(self):
        result = super().shutdown()
        directory = os.environ.get("CACHEPILOT_PROTECTION_SUMMARY_PATH")
        if directory and self._protection_summary is not None:
            path = Path(directory)
            path.mkdir(parents=True, exist_ok=True)
            target = path / f"protection-{os.getpid()}-{self._handoff_role}.json"
            target.write_text(json.dumps(self._protection_summary, indent=2, sort_keys=True) + "\n")
        return result

    def _apply_protection(self, metadata, scheduler_output):
        return metadata

    def bind_kv_cache_manager(self, kv_cache_manager):
        super().bind_kv_cache_manager(kv_cache_manager)
        if getattr(self, "_bounded", None) is not None:
            from scripts.bounded_protection import install_protection_scheduler
            from vllm.v1.core.sched.scheduler import Scheduler
            self._bounded.bind(kv_cache_manager)
            install_protection_scheduler(Scheduler)

    def build_connector_meta(self, scheduler_output):
        metadata = super().build_connector_meta(scheduler_output)
        before = [describe(m) for m in metadata.requests]
        metadata = self._apply_protection(metadata, scheduler_output)
        after = [describe(m) for m in metadata.requests]
        if before != after:
            raise RuntimeError("Protection postprocessing changed the default metadata")
        batches = []
        for row in after:
            self._handoff_record("scheduler_metadata", **row)
            if row["direction"] != "STORE":
                continue
            self._handoff_serial += 1
            bounded = getattr(self, "_bounded", None)
            source = "protection" if bounded and row["request_id"] in bounded.committed_this_step else "default_manager"
            batch = dict(row, batch_id=f"{row['wire_id']}:{self._handoff_serial}", source=source)
            self._handoffs.accept(batch, "scheduler_built")
            batches.append(batch)
        metadata.cachepilot_handoffs_v1 = batches
        return metadata

    def bind_connector_metadata(self, metadata):
        batches = getattr(metadata, "cachepilot_handoffs_v1", None)
        expected = [describe(m) for m in metadata.requests if m.direction == "STORE"]
        if batches is None or [dict((k, b[k]) for k in r) for b, r in
                zip(batches, expected)] != expected or len(batches) != len(expected):
            raise ValueError("Worker metadata differs from original handoff")
        for batch in batches:
            self._handoffs.accept(batch, "worker_bound")
        self._bound_handoffs = batches
        return super().bind_connector_metadata(metadata)

    def wait_for_save(self):
        batches = self._bound_handoffs
        result = super().wait_for_save()
        for batch in batches:
            self._handoff_record("worker_submit_returned", **batch)
        self._bound_handoffs = []
        return result

    def build_connector_worker_meta(self):
        result = super().build_connector_worker_meta()
        if result is None:
            return None
        batches = self._handoffs.verify(result.completed_store_requests, result.failed_store_requests)
        envelope = HandoffWorkerMetadata(dict(result.completed_store_requests),
            set(result.failed_store_requests), {r: b["batch_id"] for r, b in batches.items()})
        self._handoffs.retire(batches, result.failed_store_requests, "worker_receipt")
        return envelope

    def update_connector_output(self, connector_output):
        meta = connector_output.kv_connector_worker_meta
        if meta is None:
            return super().update_connector_output(connector_output)
        if not isinstance(meta, HandoffWorkerMetadata):
            raise ValueError("Missing original STORE receipt envelope")
        batches = self._handoffs.verify(meta.completed_store_requests,
            meta.failed_store_requests, meta.batch_ids)
        result = super().update_connector_output(connector_output)
        bounded = getattr(self, "_bounded", None)
        if bounded is not None:
            bounded.receipts(batches, meta.failed_store_requests)
        self._handoffs.retire(batches, meta.failed_store_requests, "scheduler_receipt")
        return result


class ProtectionConnector(HandoffObservedConnector):
    def _apply_protection(self, metadata, scheduler_output):
        if self._protection_config.enabled and self._protection_config.max_chunks:
            return metadata
        # Zero budget is a real, explicitly supported bypass. Do not install
        # the CPU pool harness, create a candidate, touch a block, or STORE.
        if metadata.requests:
            self._handoff_record("protection_bypass",
                reason="zero_budget" if self._protection_config.enabled else "disabled",
                scheduled_tokens=scheduler_output.total_num_scheduled_tokens,
                added_stores=0, acquired_protection_pins=0)
        return metadata
