import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from analyze_decision_timeline import analyze


class TimelineTests(unittest.TestCase):
    def test_suffix_and_later_miss_are_separate(self):
        common=dict(request_id='source',start=0,end=256,prefix_sha256='prefix')
        rows=[dict(event='request',request_id='source',prompt_tokens=260,prefixes={'256':'prefix'}),
              dict(event='request',request_id='next',prompt_tokens=300,prefixes={'256':'prefix'}),
              dict(event='drain',step=1,danger_depth=3,new_blocks=0,free_blocks=40,
                   pending=[dict(**common,nearest_free_rank=20,blocked=False)]),
              dict(event='allocation',step=1,upcoming_step=2,monotonic_ns=10,allocated_blocks=128,
                   context={'async_load':True},affected=[dict(**common,recycled_block_ids=[1],intact_before_ids=[1])]),
              dict(event='lookup',request_id='next',monotonic_ns=11,step=2,gpu_computed_tokens=0,external_tokens=0),
              dict(event='scheduled',step=2,actual_allocated_blocks=128),
              dict(event='dropped_evicted',**common,step=2,monotonic_ns=12,changed_block_ids=[1],
                   first_lost=True,drop_kind='hash_changed',age_seconds=.2),
              dict(event='drain',step=2,danger_depth=3,new_blocks=0,free_blocks=10,pending=[])]
        result=analyze(rows)
        self.assertEqual(result['allocator_confirmed_operations'],1)
        self.assertEqual(result['drops_with_later_miss'],1)
        self.assertEqual(result['cases'][0]['prior']['rank'],20)
        self.assertEqual(result['cases'][0]['reported_new_blocks'],0)
        rows[4]['monotonic_ns']=9  # Lookup predates known loss: not a later miss.
        self.assertEqual(analyze(rows)['drops_with_later_miss'],0)
        rows[6].update(changed_block_ids=[],first_lost=False,drop_kind='prefix_suffix')
        self.assertEqual(analyze(rows)['suffix_without_hash_change'],1)

    def test_missing_allocation_is_not_proof(self):
        row=dict(event='dropped_evicted',request_id='s',start=0,end=256,prefix_sha256='p',step=1,
                 monotonic_ns=10,changed_block_ids=[1],age_seconds=.2)
        result=analyze([row])
        self.assertEqual(result['allocator_confirmed_operations'],0)
        self.assertIsNone(result['first_lost_operations'])
        self.assertIsNone(result['cases'][0]['prior'])


if __name__=='__main__':unittest.main()
