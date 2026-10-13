"""Independent CPU candidate; do not configure a serving Connector with it."""
from cachepilot.runtime.shared_prefix import SharedPrefixProtection
from cachepilot.runtime.whole_step import WholeStepAdmission


class WholeStepProtection(SharedPrefixProtection):
    admission_policy = WholeStepAdmission()

    def bind(self, kv_manager):
        raise RuntimeError('Whole-step candidate has not passed service admission')
