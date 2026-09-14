<div align="center">

<h1>
  Smart Thermostat
</h1>

### UI-configurable PID thermostat control for Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-CUSTOM-FD7E14?style=for-the-badge&logo=home-assistant&logoColor=white&labelColor=555555)](https://github.com/hacs/integration)
[![Home Assistant Custom Integration](https://img.shields.io/badge/HOME%20ASSISTANT-CUSTOM%20INTEGRATION-41BDF5?style=for-the-badge&logo=home-assistant&logoColor=white&labelColor=555555)](https://www.home-assistant.io/)
[![Latest release](https://img.shields.io/github/v/release/Wheemer/HASmartThermostat?style=for-the-badge&logo=github&logoColor=white&label=RELEASE&labelColor=555555&color=22C55E)](https://github.com/Wheemer/HASmartThermostat/releases/latest)
[![Downloads](https://img.shields.io/github/downloads/Wheemer/HASmartThermostat/total?style=for-the-badge&logo=github&logoColor=white&label=DOWNLOADS&labelColor=555555&color=8A2BE2)](https://github.com/Wheemer/HASmartThermostat/releases)
[![License: MIT](https://img.shields.io/badge/LICENSE-MIT-64748B?style=for-the-badge&labelColor=555555)](LICENSE)

[Install](#install) - [Configure](#configure) - [YAML Import](#yaml-import) - [Services](#services) - [Development](#development)

</div>

This fork is based on [ScratMan/HASmartThermostat](https://github.com/ScratMan/HASmartThermostat). It keeps the original PID thermostat behavior while adding UI setup, lossless YAML import, per-thermostat options, safer output handling, and adaptive learning hooks.

Smart Thermostat creates a Home Assistant climate entity that drives one or more heating or cooling outputs from a temperature sensor. It supports on/off switches through PWM and proportional `valve` or `light` outputs.

## What It Does

- Creates PID-backed climate entities for heating, cooling, or heat/cool setups.
- Imports existing YAML thermostats into config entries without replacing later UI edits.
- Lets each thermostat be configured from **Settings > Devices & services**.
- Preserves independent preset temperatures per thermostat.
- Supports `switch`, `valve`, and `light` outputs.
- Supports PWM output, proportional output, outdoor compensation, min-cycle guards, output safety, sensor-stall protection, and optional legacy autotune.
- Adds optional adaptive observation and PID learning diagnostics from recent Home Assistant history.
- Restores runtime PID gains, presets, target temperature, and learning state after reload or restart.
- Can be unloaded and reloaded as a Home Assistant config entry.

## Install

[![Open your Home Assistant instance and add this repository to HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Wheemer&repository=HASmartThermostat&category=integration)

If the button does not work:

1. Open HACS.
2. Add `Wheemer/HASmartThermostat` as a custom integration repository.
3. Install **Smart Thermostat**.
4. Restart Home Assistant once so the custom integration is loaded.
5. Add or import thermostats from **Settings > Devices & services**.

## Manual Installation

Copy the integration folder into Home Assistant:

```text
/config/custom_components/smart_thermostat
```

Restart Home Assistant, then add **Smart Thermostat** from:

```text
Settings > Devices & services > Add integration
```

## Configure

Each thermostat has its own config entry and its own options. Open:

```text
Settings > Devices & services > Smart Thermostat > Configure
```

The options are grouped into sections:

- **Controller:** name, heating outputs, cooling outputs, indoor sensor, outdoor sensor, AC mode, inverted output, and force-off behavior.
- **Temperatures:** min/max limits, target temperature, hot/cold tolerances, display precision, target step, and initial HVAC mode.
- **Presets:** away, eco, boost, comfort, home, sleep, and activity preset temperatures.
- **Timing:** keep-alive interval, min on/off cycle durations, sampling period, sensor-stall timeout, and PWM period.
- **PID and learning:** configured PID gains, derivative filtering, outdoor compensation gain, adaptive observation, adaptive learning, legacy autotune, noiseband, lookback, and boost PID behavior.
- **Output and diagnostics:** output precision, output min/max, output clamps, output safety value, and debug logging.

## YAML Import

Existing YAML thermostats are imported automatically through Home Assistant's config-entry import flow. After import, edit the thermostat through the UI.

Example YAML that can be imported:

```yaml
climate:
  - platform: smart_thermostat
    name: Main Heat
    unique_id: main_heat
    heater: switch.furnace_heat
    target_sensor: sensor.house_temperature_average
    min_temp: 7
    max_temp: 28
    target_temp: 22
    cold_tolerance: 0.1
    hot_tolerance: 0
    kp: 50
    ki: 0.01
    kd: 2000
    pwm:
      minutes: 15
    keep_alive:
      seconds: 60
```

Imported YAML is kept as the initial source only. A leftover YAML entry with the same unique ID will not overwrite later UI edits.

## PID Control

The PID controller calculates output from the thermostat error:

```text
error = target temperature - current temperature
output = P + I + D + optional outdoor compensation
```

For PWM outputs, the control output becomes a portion of the PWM window. For proportional `valve` or `light` outputs, the control output is sent directly as the target percentage.

This fork includes fixes for several upstream edge cases:

- sampled PID integral uses the configured sampling period instead of a stale sensor gap;
- cooler demand increases as the room gets hotter;
- min-cycle timing still applies to same-second output reversals;
- rejected or unavailable output commands do not reset PWM timing;
- output min/max are honored by the effective clamps;
- optional derivative filtering can smooth one-off temperature sensor spikes without changing default PID behavior;
- `turn_on` and `turn_off` climate services restore the correct active HVAC mode;
- preset service changes correctly expose or remove preset support;
- legacy autotune continues advancing on periodic control ticks.

## Adaptive Learning

Adaptive learning is optional and conservative. The thermostat can observe completed heating or cooling sessions, import recent Home Assistant history, and expose learning diagnostics in entity attributes.

When enabled, the adaptive logic is designed to adjust PID gains only from completed, validated cycles. It does not replace the thermostat automation model and does not command outputs directly.

## Services

The integration provides these services:

- `smart_thermostat.set_pid_gain`: adjust `kp`, `ki`, `kd`, or `ke`.
- `smart_thermostat.set_pid_mode`: switch PID mode between `auto` and `off`.
- `smart_thermostat.set_preset_temp`: set or disable preset temperatures.
- `smart_thermostat.clear_integral`: clear the PID integral term.

Example:

```yaml
service: smart_thermostat.set_pid_gain
target:
  entity_id: climate.main_heat
data:
  kp: 40
  ki: 0.005
  kd: 1500
```

## Development

Run the standalone test suite from the repository root:

```bash
python -m pytest tests
```

Compile the main integration files:

```bash
python -m py_compile custom_components/smart_thermostat/climate.py custom_components/smart_thermostat/pid_controller/__init__.py
```

## Attribution

Originally created by [ScratMan](https://github.com/ScratMan/HASmartThermostat). This fork keeps that foundation and carries local fixes, UI configuration work, and adaptive-learning experiments for Home Assistant systems that need tighter thermostat behavior.
