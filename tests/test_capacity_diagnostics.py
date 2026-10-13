import os
from pathlib import Path
import sys
import ast

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from scripts.capacity_diagnostic_protection import DiagnosticBoundedProtection as BoundedProtection


class Connector:
    def __init__(self): self.events = []
    def _handoff_record(self, event, **fields): self.events.append((event, fields))


def test_capacity_diagnostic_is_opt_in_and_before_dedup(monkeypatch):
    connector = Connector()
    obj = object.__new__(BoundedProtection)
    obj.connector = connector
    obj.record = connector._handoff_record
    obj._capacity_observed = set()
    obj.budget = 16
    monkeypatch.setattr('scripts.capacity_diagnostic_protection.describe', lambda meta: dict(wire_id='w1'))
    meta = object()
    monkeypatch.delenv('CACHEPILOT_CAPACITY_DIAGNOSTICS', raising=False)
    obj._capacity_fallback(meta, 16, 10, 20, 16, 0)
    assert [e[0] for e in connector.events] == ['protection_fallback']
    connector.events.clear(); obj._capacity_observed.clear()
    monkeypatch.setenv('CACHEPILOT_CAPACITY_DIAGNOSTICS', '1')
    obj._capacity_fallback(meta, 16, 10, 20, 16, 0)
    obj._capacity_fallback(meta, 16, 10, 20, 16, 0)
    assert [e[0] for e in connector.events] == [
        'protection_capacity_diagnostic', 'protection_fallback',
        'protection_capacity_diagnostic']
    assert connector.events[0][1]['free_minus_reserve_minus_chunk'] == -26


def test_diagnostic_copy_preserves_original_decision_ast():
    root = Path(__file__).resolve().parents[1]
    base = ast.parse((root / 'src/cachepilot/runtime/protection.py').read_text())
    observed = ast.parse((root / 'scripts/capacity_diagnostic_protection.py').read_text())

    class Strip(ast.NodeTransformer):
        def visit_If(self, node):
            if 'CACHEPILOT_CAPACITY_DIAGNOSTICS' in ast.unparse(node.test):
                return None
            return self.generic_visit(node)

        def visit_Expr(self, node):
            if isinstance(node.value, ast.Call) and ast.unparse(node.value.func) == 'self._diagnostic':
                return None
            return self.generic_visit(node)

    def methods(tree):
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name.endswith('BoundedProtection'))
        return {n.name:n for n in cls.body if isinstance(n, ast.FunctionDef)}

    old, new = methods(base), methods(observed)
    for name in ('prepare', '_capacity_fallback'):
        assert ast.dump(Strip().visit(new[name]), include_attributes=False) == ast.dump(old[name], include_attributes=False)
