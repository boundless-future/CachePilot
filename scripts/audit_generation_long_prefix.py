"""Require full protected-prefix/default-suffix provenance and ledger lineage."""
import json
from pathlib import Path
from scripts.audit_generation_protection_smoke import audit_cell
from scripts.audit_protection_long_prefix import audit as audit_split


def audit(directory):
    directory = Path(directory)
    normal = audit_cell(directory)
    split = audit_split(directory)
    assert normal['passed'] and split['passed']
    snapshot = json.loads((directory / 'generation.json').read_text(encoding='utf-8'))
    generations = {g['wire_id']: g for g in snapshot['generations']}
    links = snapshot['derived_tickets']
    assert len({link['serial'] for link in links}) == len(links)
    for link in links:
        ops = {op['serial']: op for op in generations[link['wire_id']]['operations']}
        assert link['serial'] != link['parent_serial']
        assert ops[link['serial']]['kind'] == ops[link['parent_serial']]['kind'] == 'pending_store'
        assert ops[link['serial']]['state'] in ('terminal', 'dropped')
        assert ops[link['parent_serial']]['state'] == 'terminal'
    for chain in split['strict_complete_chains']:
        ops = {op['serial']: op for op in generations[chain['wire_id']]['operations']}
        assert any(link['wire_id'] == chain['wire_id']
            and ops[link['serial']]['state'] == 'terminal' for link in links), 'Missing terminal suffix lineage'
    return dict(passed=True, normal=normal, split=split, derived_tickets=len(links))
