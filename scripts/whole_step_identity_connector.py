"""Opt-in per-request cache-match observations for the fixed-trace diagnosis."""
from types import SimpleNamespace

from cachepilot.integration.lmcache.protocol import wire_id
from cachepilot.integration.vllm.whole_step import WholeStepConnector


class WholeStepIdentityConnector(WholeStepConnector):
    def get_num_new_matched_tokens(self, request, num_computed_tokens):
        matched = super().get_num_new_matched_tokens(request, num_computed_tokens)
        tracker = self.request_trackers.get(request.request_id)
        identity = None
        if tracker is not None:
            identity = wire_id(SimpleNamespace(request_id=request.request_id,
                                               request_configs=tracker.request_configs))
        prompt_ids = getattr(request, "prompt_token_ids", None) or ()
        external_tokens, async_load = matched
        self._handoff_record(
            "request_cache_match",
            request_id=request.request_id,
            wire_id=identity,
            prompt_tokens=len(prompt_ids),
            gpu_computed_tokens=num_computed_tokens,
            external_tokens=external_tokens,
            async_load=async_load,
        )
        return matched
