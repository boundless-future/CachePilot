import sys
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from analyze_allocation_signal import audit


class AuditTests(unittest.TestCase):
    def test_idle_carry_and_resume_checked_against_independent_events(self):
        rows=[dict(event='allocation',allocated_blocks=128),
              dict(event='scheduled',step=1,tokens=0,actual_allocated_blocks=128),
              dict(event='scheduled',step=2,tokens=1,actual_allocated_blocks=0),
              dict(event='allocation_signal',step=2,original_new_blocks=128,
                   actual_step_blocks=0,consumed_blocks=128,total_allocated=128,total_consumed=128),
              dict(event='drain',step=2,new_blocks=128)]
        self.assertTrue(audit(rows)['passed'])
        rows[3]['consumed_blocks']=256
        self.assertFalse(audit(rows)['passed'])

    def test_no_evidence_cannot_pass(self):
        self.assertFalse(audit([])['passed'])


if __name__=='__main__':unittest.main()
