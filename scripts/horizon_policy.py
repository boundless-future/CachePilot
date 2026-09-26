"""Small policy adapter independent of vLLM, logging and GPU libraries."""
from dataclasses import dataclass, replace
import math


@dataclass(frozen=True)
class HorizonSettings:
    base: float
    wide: float = 5.0
    threshold: float = 16.0

    def __post_init__(self):
        if not all(math.isfinite(v) and v > 0 for v in (self.base, self.wide, self.threshold)):
            raise ValueError('Horizons and threshold must be finite positive numbers')
        if self.wide < self.base:
            raise ValueError('Wide horizon must be at least the base horizon')

    def select(self, signals):
        return self.wide if max(signals.new_blocks_allocated, signals.est_next_step_blocks) >= self.threshold else self.base


def install_horizon(policy, settings):
    """Install once; keep upstream drain/cleanup semantics and restore on error."""
    installed = getattr(policy, '_cachepilot_horizon', None)
    if installed is not None:
        if installed != settings:
            raise ValueError('Cannot rebind a policy with different horizon settings')
        return
    original = policy.drain

    def drain(signals):
        old = policy._config
        policy._config = replace(old, horizon_steps=settings.select(signals))
        try:
            return original(signals)
        finally:
            policy._config = old

    policy.drain = drain
    policy._cachepilot_horizon = settings
