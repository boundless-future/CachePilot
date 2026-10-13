"""Limit the candidate's source delta to the reviewed reference semantics."""
import ast
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def methods(path,name):
    tree=ast.parse(path.read_text())
    cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name==name)
    return {n.name:n for n in cls.body if isinstance(n,ast.FunctionDef)}


def test_shared_candidate_keeps_reserve_hash_proof_and_commit_logic():
    base=methods(ROOT/'src/cachepilot/runtime/protection.py','BoundedProtection')
    shared=methods(ROOT/'src/cachepilot/runtime/shared_prefix.py','SharedPrefixProtection')
    expected=ast.unparse(base['prepare'])
    expected=expected.replace('    from vllm.v1.core import kv_cache_utils',
        '    self.manager._healthy()\n    from vllm.v1.core import kv_cache_utils')
    expected=expected.replace('b.ref_cnt or b.is_null','b.ref_cnt < 0 or b.is_null')
    expected=expected.replace('{bid: all_ranks[bid] for bid in ids[:protected_count]}',
        '{bid: all_ranks[bid] for bid in ids[:protected_count] if bid in all_ranks}')
    expected=expected.replace('        self.pool.touch(blocks)',
        '        refs_before = [b.ref_cnt for b in blocks]\n        self.pool.touch(blocks)')
    expected=expected.replace('free_after=self.pool.get_num_free_blocks())',
        'free_after=self.pool.get_num_free_blocks(), refs_before=refs_before, '
        'refs_after=[b.ref_cnt for b in blocks], shared_blocks_before=sum(ref > 0 for ref in refs_before))')
    assert ast.dump(ast.parse(expected).body[0])==ast.dump(shared['prepare'])
    assert set(shared)=={'prepare','rollback','bind'}


def test_rollback_restores_only_recorded_free_queue_ranks():
    base=methods(ROOT/'src/cachepilot/runtime/protection.py','BoundedProtection')
    shared=methods(ROOT/'src/cachepilot/runtime/shared_prefix.py','SharedPrefixProtection')
    expected=ast.unparse(base['rollback']).replace('    restore = []','    self.manager._healthy()\n    restore = []')
    expected=expected.replace('if b.ref_cnt == 0)', 'if b.ref_cnt == 0 and b.block_id in prepared.free_ranks)')
    assert ast.dump(ast.parse(expected).body[0])==ast.dump(shared['rollback'])
