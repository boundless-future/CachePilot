from cachepilot.config import SharedPrefixConfig, WholeStepConfig
from cachepilot.cli import main
import json


def test_whole_step_is_explicit_and_cli_matches_config(capsys):
    assert SharedPrefixConfig().kv_transfer_config()['kv_connector'] == 'SharedPrefixConnector'
    main(['config', '--whole-step', '--max-chunks', '1'])
    config = json.loads(capsys.readouterr().out)
    assert config == WholeStepConfig(max_chunks=1).kv_transfer_config()
    assert config['kv_connector'] == 'WholeStepConnector'
    assert config['kv_connector_extra_config']['cachepilot.whole_step_protection'] is True
