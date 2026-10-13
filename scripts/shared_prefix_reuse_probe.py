"""Old receipt injection through the independent generation-aware callback."""
from types import SimpleNamespace
from scripts.protection_reuse_probe import ReuseProbeConnector
from scripts.protection_connector import HandoffWorkerMetadata
from scripts.shared_prefix_connector import SharedPrefixConnector


class SharedPrefixReuseProbeConnector(ReuseProbeConnector, SharedPrefixConnector):
    def _reject_old(self, label, failed, new_batch_id=None):
        target = self._reuse_target
        rid = target['request_id']
        before = self._reuse_state()
        generations = self.generation_snapshot()
        envelope = HandoffWorkerMetadata({rid: 1}, {rid} if failed else set(),
            {rid: target['batch_id']})
        try:
            SharedPrefixConnector.update_connector_output(self,
                SimpleNamespace(kv_connector_worker_meta=envelope))
        except ValueError as exc:
            after = self._reuse_state()
            if before != after or generations != self.generation_snapshot():
                raise RuntimeError('Old envelope mutated successor') from exc
            self._handoff_record('reuse_old_rejected', case=label,
                old_batch_id=target['batch_id'], new_batch_id=new_batch_id,
                request_id=rid, error=str(exc), before=before, after=after,
                generation_before=generations, generation_after=self.generation_snapshot())
        else:
            raise RuntimeError('Old envelope was accepted for successor')
