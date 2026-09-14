"""Config-entry migration contracts without a running Home Assistant."""

import ast
from datetime import timedelta
from enum import StrEnum
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components/smart_thermostat"
spec = importlib.util.spec_from_file_location("ui_configuration", COMPONENT / "ui_configuration.py")
ui = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ui)


class ConfigurationTests(unittest.TestCase):
    def test_json_round_trip_preserves_fractional_durations_and_all_presets(self):
        values = {"unique_id": "house_thermostat", "heater": ["input_boolean.furnace_boolean"],
                  "min_cycle_duration": timedelta(minutes=2.5), "keep_alive": timedelta(seconds=60)}
        values.update({key: 20.2 + index / 10 for index, key in enumerate(ui.PRESETS)})
        result = json.loads(json.dumps(ui.serialize_configuration(values)))
        self.assertEqual(result["min_cycle_duration"], {"seconds": 150.0})
        self.assertEqual(result["sleep_temp"], values["sleep_temp"])
        self.assertEqual(result["unique_id"], "house_thermostat")
        self.assertEqual(result["heater"], values["heater"])

    def test_enum_and_platform_metadata(self):
        class Mode(StrEnum):
            HEAT = "heat"
        self.assertEqual(ui.serialize_configuration({"initial_hvac_mode": Mode.HEAT,
                                                     "platform": "smart_thermostat"}),
                         {"initial_hvac_mode": "heat"})

    def test_nonfinite_setting_rejected(self):
        with self.assertRaises(ValueError):
            ui.serialize_configuration({"kp": float("nan")})

    def test_optional_removal_does_not_revert_to_imported_value(self):
        result = ui.replace_section({"sleep_temp": 19.5, "away_temp": 18,
                                     "target_sensor": "sensor.average_temperature"},
                                    "presets", {"sleep_temp": 20.2})
        self.assertNotIn("away_temp", result)
        self.assertEqual(result["target_sensor"], "sensor.average_temperature")

    def test_section_cannot_change_identity(self):
        with self.assertRaises(ValueError):
            ui.replace_section({}, "controller", {"unique_id": "new_id"})

    def test_first_import_keeps_runtime_sleep_and_gains(self):
        old = {"sleep_temp": 20.2, "kp": 120, "temperature": 21.2}
        self.assertEqual(ui.restore_attributes(old, {"sleep_temp": 19.5, "kp": 100}), old)

    def test_unrelated_edit_preserves_learned_gains_and_synced_sleep(self):
        old = {"configured_settings": {"sleep_temp": 19.5, "kp": 100},
               "sleep_temp": 20.2, "kp": 120}
        result = ui.restore_attributes(old, {"sleep_temp": 19.5, "kp": 100, "debug": True})
        self.assertEqual(result["kp"], 120)
        self.assertEqual(result["sleep_temp"], 20.2)

    def test_explicit_edit_overrides_old_gain_and_clears_integral(self):
        old = {"configured_settings": {"kp": 100}, "kp": 120, "Kp": 120, "pid_i": 30}
        result = ui.restore_attributes(old, {"kp": 90})
        self.assertEqual(result["kp"], 90)
        self.assertNotIn("Kp", result)
        self.assertNotIn("pid_i", result)
        self.assertEqual(old["kp"], 120)

    def test_explicit_preset_removal_does_not_restore_it(self):
        old = {"configured_settings": {"sleep_temp": 19.5}, "sleep_temp": 20.2}
        self.assertNotIn("sleep_temp", ui.restore_attributes(old, {}))

    def test_removing_selected_preset_clears_selection_but_preserves_target(self):
        old = {"configured_settings": {"sleep_temp": 19.5}, "sleep_temp": 20.2,
               "preset_mode": "sleep", "temperature": 20.2}
        restored = ui.restore_attributes(old, {})
        self.assertEqual(restored["preset_mode"], "none")
        self.assertEqual(restored["temperature"], 20.2)

    def test_every_existing_yaml_setting_has_a_ui_field(self):
        constants = {}
        exec((COMPONENT / "const.py").read_text(), constants)
        tree = ast.parse((COMPONENT / "climate.py").read_text())
        schema = next(node for node in tree.body if isinstance(node, ast.Assign)
                      and any(isinstance(t, ast.Name) and t.id == "PLATFORM_SCHEMA" for t in node.targets))
        keys = []
        for key in schema.value.args[0].keys:
            value = key.args[0]
            if isinstance(value, ast.Constant):
                keys.append(value.value)
            elif isinstance(value, ast.Attribute):
                keys.append(constants[value.attr])
            else:
                keys.append({"CONF_NAME": "name", "CONF_UNIQUE_ID": "unique_id"}[value.id])
        self.assertEqual(set(keys) - {"unique_id"}, set(ui.FIELDS))

    def test_translations_cover_every_form_and_match(self):
        strings = json.loads((COMPONENT / "strings.json").read_text())
        self.assertEqual(strings, json.loads((COMPONENT / "translations/en.json").read_text()))
        for section, fields in ui.SECTIONS.items():
            self.assertEqual(set(strings["options"]["step"][section]["data"]), set(fields))


if __name__ == "__main__":
    unittest.main()
