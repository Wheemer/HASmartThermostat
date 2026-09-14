"""Load the pinned inspiration source directly, without its HA package setup.

Package shells avoid importing HA-dependent __init__.py modules. Physics,
constants and PID code execute unchanged from the inspected reference checkout.
This is an offline compatibility harness, not a runtime dependency.
"""

import importlib
from pathlib import Path
import sys
from types import ModuleType


REFERENCE = Path(__file__).resolve().parents[2] / 'adaptive-climate-review' / 'custom_components' / 'adaptive_climate'
PACKAGE = '_reference_adaptive_compatibility'
for name, path in ((PACKAGE, REFERENCE), (PACKAGE + '.adaptive', REFERENCE / 'adaptive')):
    if name not in sys.modules:
        shell = ModuleType(name)
        shell.__path__ = [str(path)]
        sys.modules[name] = shell

physics = importlib.import_module(PACKAGE + '.adaptive.physics')
pid_module = importlib.import_module(PACKAGE + '.pid_controller')
constants = importlib.import_module(PACKAGE + '.const')
ReferencePID = pid_module.PID
