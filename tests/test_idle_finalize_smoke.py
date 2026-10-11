import pytest

from scripts.idle_finalize_smoke import configure_probe


@pytest.mark.parametrize('name,expected', [
    ('ReceiptProbeConnector','IdleReceiptProbeConnector'),
    ('SharedCancelProbeConnector','IdleSharedCancelProbeConnector')])
def test_composition_preserves_existing_policy_and_input(name,expected):
    config=dict(kv_connector=name,kv_connector_module_path='original',
                kv_connector_extra_config={'cachepilot.protection.max_chunks':2,'other':{'value':3}})
    result=configure_probe(config,15)
    assert result['kv_connector']==expected
    assert result['kv_connector_extra_config']['cachepilot.protection.max_chunks']==2
    assert result['kv_connector_extra_config']['cachepilot.idle_finalize'] is True
    result['kv_connector_extra_config']['other']['value']=4
    assert config['kv_connector_module_path']=='original'
    assert config['kv_connector_extra_config']=={'cachepilot.protection.max_chunks':2,'other':{'value':3}}


@pytest.mark.parametrize('grace',[0,-1,float('nan'),float('inf')])
def test_invalid_grace_fails_before_mutation(grace):
    with pytest.raises(ValueError): configure_probe({},grace)


def test_unknown_probe_not_silently_replaced():
    with pytest.raises(ValueError,match='Unsupported'):
        configure_probe({'kv_connector':'UnreviewedConnector'},15)
