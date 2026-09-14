"""Protect the user's installed thermostat interface during internal learning work."""

import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


def methods(path):
    tree = ast.parse(path.read_text(encoding='utf-8'))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'SmartThermostat')
    # Lifecycle guards wrap the same command bodies; their coverage is tested separately.
    for node in cls.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            node.decorator_list = [d for d in node.decorator_list
                                   if not isinstance(d, ast.Name) or d.id != 'entity_operation']
    return {n.name: ast.dump(n, include_attributes=False) for n in cls.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


class CompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.before = methods(ROOT / 'installed-snapshot/climate.py')
        cls.after = methods(ROOT / 'custom_components/smart_thermostat/climate.py')

    def unchanged(self, names):
        for name in names:
            with self.subTest(method=name):
                self.assertEqual(self.before[name], self.after[name])

    def test_presets_including_sleep_and_sync_unchanged(self):
        self.unchanged(('preset_mode', 'preset_modes', 'presets', '_preset_modes_temp',
                        '_preset_temp_modes', 'async_set_preset_mode'))

    def test_identity_and_display_unchanged(self):
        self.unchanged(('name', 'unique_id', 'precision', 'target_temperature_step',
                        'temperature_unit', 'current_temperature', 'target_temperature'))

    def test_existing_commands_unchanged(self):
        self.unchanged(('async_set_pid_mode', 'clear_integral'))

    def test_minimum_cycle_and_output_selection_unchanged(self):
        self.unchanged(('_min_on_cycle_duration', '_min_off_cycle_duration',
                        'heater_or_cooler_entity'))

    def test_service_definitions_and_domain_unchanged(self):
        for name in ('services.yaml', 'const.py'):
            with self.subTest(file=name):
                before = (ROOT / 'installed-snapshot' / name).read_text(encoding='utf-8')
                after = (ROOT / 'custom_components/smart_thermostat' / name).read_text(encoding='utf-8')
                self.assertEqual(before, after)


if __name__ == '__main__':
    unittest.main()
