"""Pure physical furnace-response history tests."""

import importlib.util
from pathlib import Path
import sys
import types
import unittest


ROOT = Path(__file__).resolve().parents[1] / 'custom_components' / 'smart_thermostat'
PACKAGE = '_furnace_history_test'
package = types.ModuleType(PACKAGE)
package.__path__ = [str(ROOT)]
sys.modules[PACKAGE] = package
spec = importlib.util.spec_from_file_location(f'{PACKAGE}.furnace_history', ROOT / 'furnace_history.py')
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
replay = module.replay_furnace_response_history


def row(timestamp, state, **extra):
    return {'timestamp': timestamp, 'state': state, **extra}


def history():
    return {
        'sensor.room': [row(t, str(20 + min(t, 900) / 1800)) for t in range(0, 1501, 30)],
        'sensor.furnace': [row(t, str(22 + min(t, 360) / 12)) for t in range(0, 1501, 30)],
        'switch.heat': [row(0, 'off'), row(60, 'on'), row(360, 'off')],
    }


class FurnaceHistoryTests(unittest.TestCase):
    def test_replays_a_complete_physical_cycle_without_climate_history(self):
        records, report = replay(history(), 'sensor.room', ['switch.heat'], 'sensor.furnace', 0, 1500)
        self.assertEqual(report['status'], 'ok')
        self.assertEqual(report['completed_cycles'], 1)
        self.assertEqual(records[0]['runtime_seconds'], 300)
        self.assertEqual(records[0]['furnace_baseline'], 27.0)
        self.assertGreater(records[0]['coast'], 0)

    def test_manual_cycle_is_not_used(self):
        data = history()
        data['switch.heat'][1]['user_id'] = 'person'
        records, report = replay(data, 'sensor.room', ['switch.heat'], 'sensor.furnace', 0, 1500)
        self.assertEqual(records, [])
        self.assertGreater(report['rejections']['manual_output_change'], 0)

    def test_missing_furnace_history_is_not_modelled(self):
        data = history()
        del data['sensor.furnace']
        records, report = replay(data, 'sensor.room', ['switch.heat'], 'sensor.furnace', 0, 1500)
        self.assertEqual(records, [])
        self.assertEqual(report['status'], 'missing_history_entities')
