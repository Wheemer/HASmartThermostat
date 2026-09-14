"""Attribute consistency checks for dashboard/debug output."""

import ast
from pathlib import Path
from types import SimpleNamespace
import unittest


SOURCE = Path(__file__).resolve().parents[1] / "custom_components/smart_thermostat/climate.py"
tree = ast.parse(SOURCE.read_text())
thermostat = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "SmartThermostat")
method = next(n for n in thermostat.body if isinstance(n, ast.FunctionDef)
              and n.name == "extra_state_attributes")
namespace = {}
exec(compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])),
             str(SOURCE), "exec"), namespace)
extra_state_attributes = namespace["extra_state_attributes"].fget


class AttributeTests(unittest.TestCase):
    def test_debug_external_term_uses_output_precision(self):
        thermostat = SimpleNamespace(
            _away_temp=None,
            _eco_temp=None,
            _boost_temp=None,
            _comfort_temp=None,
            _home_temp=None,
            _sleep_temp=None,
            _activity_temp=None,
            _control_output=30.1,
            _kp=1,
            _ki=2,
            _kd=3,
            _ke=4,
            pid_mode="auto",
            _autotune="none",
            pid_control_i=10.0,
            _configured_settings=None,
            _observer=None,
            _debug=True,
            pid_control_p=0.0,
            pid_control_d=0.0,
            pid_control_e=99.0,
            _e=20.456,
            _output_precision=1,
            _dt=30,
        )

        attrs = extra_state_attributes(thermostat)

        self.assertEqual(attrs["pid_e"], 20.5)


if __name__ == "__main__":
    unittest.main()
