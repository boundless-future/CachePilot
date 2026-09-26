import sys
import unittest
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from horizon_policy import HorizonSettings, install_horizon


@dataclass(frozen=True)
class Config:
    horizon_steps: float = 2.5
    max_drain_per_step: int = 64


class Policy:
    def __init__(self):
        self._config = Config()
        self.calls = []

    def drain(self, signals):
        self.calls.append(self._config)
        if signals.new_blocks_allocated < 0:raise RuntimeError('upstream failure')
        return signals


class HorizonTests(unittest.TestCase):
    def test_both_pressure_signals_and_idle(self):
        p=Policy();old=p._config;install_horizon(p,HorizonSettings(2.5))
        for new,estimate,expected in [(15,15,2.5),(16,0,5),(0,16,5),(0,0,2.5)]:
            s=SimpleNamespace(new_blocks_allocated=new,est_next_step_blocks=estimate)
            self.assertIs(p.drain(s),s)
            self.assertEqual(p.calls[-1].horizon_steps,expected)
            self.assertEqual(p.calls[-1].max_drain_per_step,64)
            self.assertIs(p._config,old)

    def test_invalid_parameters(self):
        for values in [(0,5,16),(2.5,2,16),(2.5,5,0),(2.5,float('inf'),16),(2.5,5,float('nan'))]:
            with self.assertRaises(ValueError):HorizonSettings(*values)

    def test_rebind_does_not_stack_wrappers(self):
        p=Policy();settings=HorizonSettings(2.5);install_horizon(p,settings);wrapper=p.drain
        install_horizon(p,settings);self.assertIs(p.drain,wrapper)
        with self.assertRaises(ValueError):install_horizon(p,HorizonSettings(2.5,6))

    def test_upstream_failure_restores_config(self):
        p=Policy();old=p._config;install_horizon(p,HorizonSettings(2.5))
        with self.assertRaises(RuntimeError):p.drain(SimpleNamespace(new_blocks_allocated=-1,est_next_step_blocks=128))
        self.assertIs(p._config,old)


if __name__=='__main__':unittest.main()
