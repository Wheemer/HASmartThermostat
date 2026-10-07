"""Preset numbers remain the runtime source of truth."""

import ast
from pathlib import Path
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "custom_components/smart_thermostat/number.py"


class PresetNumberContractTests(unittest.TestCase):
    def test_numbers_restore_and_apply_values_to_the_thermostat(self):
        tree = ast.parse(SOURCE.read_text())
        number = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                      and node.name == "PresetTemperatureNumber")
        self.assertIn("RestoreEntity", [base.id for base in number.bases])
        added = next(node for node in number.body if isinstance(node, ast.AsyncFunctionDef)
                     and node.name == "async_added_to_hass")
        source = ast.unparse(added)
        self.assertIn("async_get_last_state", source)
        self.assertIn("_target_temp", source)
        setter = next(node for node in number.body if isinstance(node, ast.AsyncFunctionDef)
                      and node.name == "async_set_native_value")
        self.assertIn("async_set_preset_temp", ast.unparse(setter))

    def test_number_setup_waits_for_the_matching_thermostat(self):
        source = SOURCE.read_text()
        self.assertIn("asyncio.wait_for(ready_event.wait()", source)
        self.assertIn("ConfigEntryNotReady", source)


if __name__ == "__main__":
    unittest.main()
