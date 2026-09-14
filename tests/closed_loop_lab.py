"""Offline actual-controller harness with a synthetic delayed thermal plant.

Uses source AST methods, replacing HA states/services and the wall clock only.
Lifecycle wrappers are omitted here; dedicated lifecycle tests cover those.
Instantaneous output feedback is an explicit assumption, not device validation.
"""

import ast
import asyncio
from collections import deque
from datetime import timedelta
import logging
from math import exp, sin
from types import SimpleNamespace

from test_initial_pid_standalone import PID, ROOT


METHODS = ('_async_control_heating', 'calc_pid', 'set_control_value', 'pwm_switch',
           '_is_toggle_entity_domain', '_async_heater_turn_on', '_async_heater_turn_off')


def controller(gains, initial, target, *, pwm=900, minimum=150, mode='heat'):
    clock = SimpleNamespace(now=0)
    source = ROOT / 'climate.py'
    tree = ast.parse(source.read_text())
    thermostat = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'SmartThermostat')
    methods = [n for n in thermostat.body
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in METHODS]
    assert {n.name for n in methods} == set(METHODS)
    for method in methods:
        method.decorator_list = []
    namespace = dict(time=SimpleNamespace(time=lambda: clock.now),
                     HVACMode=SimpleNamespace(OFF='off', HEAT='heat', COOL='cool'),
                     _LOGGER=logging.getLogger('closed_loop_lab'), output_available=lambda state: state is not None,
                     ATTR_ENTITY_ID='entity_id', HA_DOMAIN='homeassistant',
                     SERVICE_TURN_ON='turn_on', SERVICE_TURN_OFF='turn_off')
    cls = ast.ClassDef(name='Controller', bases=[], keywords=[], body=methods, decorator_list=[], type_params=[])
    exec(compile(ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[])), str(source), 'exec'), namespace)
    device = namespace['Controller']()
    out_min, out_max = (-100, 0) if mode == 'cool' else (0, 100)
    output_entity = 'input_boolean.test_cool' if mode == 'cool' else 'input_boolean.test_heat'
    values = dict(_temp_lock=asyncio.Lock(), _observer=None, _adaptive_ready=False, _active=True,
                  _hvac_mode=mode, _force_off_state=True, _sensor_stall=0, _sampling_period=timedelta(0),
                  _current_temp=initial, _target_temp=target, _ext_temp=None, _e=0, _ke=0,
                  _pid_output=0, _control_output=0, _output_precision=1, _min_out=out_min, _max_out=out_max,
                  _difference=100, _pwm=pwm, _min_on_cycle_duration=timedelta(seconds=minimum),
                  _min_off_cycle_duration=timedelta(seconds=minimum), _time_changed=-pwm,
                  _last_heat_cycle_time=-minimum, _force_on=False, _force_off=False, _keep_alive=True,
                  _is_device_active=False, _heater_polarity_invert=False,
                  _is_toggle_entity_domain=lambda entity: True,
                  _heater_entity_id=None if mode == 'cool' else [output_entity],
                  _cooler_entity_id=[output_entity] if mode == 'cool' else None,
                  heater_or_cooler_entity=[output_entity], entity_id='climate.test_heat',
                  _autotune='none', _cur_temp_time=None, _previous_temp_time=None,
                  _pid_controller=PID(**gains, out_min=out_min, out_max=out_max), calls=[], transitions=[])
    device.__dict__.update(values)

    async def service(domain, name, data):
        assert domain == 'homeassistant' and data == {'entity_id': output_entity}
        assert name in ('turn_on', 'turn_off')
        on = name == 'turn_on'
        device.calls.append((clock.now, name))
        if on != device._is_device_active:
            device.transitions.append((clock.now, on))
        device._is_device_active = on

    device.hass = SimpleNamespace(states=SimpleNamespace(get=lambda _: SimpleNamespace(state='off')),
                                 services=SimpleNamespace(async_call=service))
    device.async_write_ha_state = lambda: None
    return device, clock


async def simulate(gains, *, rate=6.45, delay=300, tau=30, drift=-0.15,
                   initial=21.5, target=22, seconds=21600, noise=0, pwm=900, minimum=150):
    """One-second plant integration; actual controller gets samples every 30s."""
    if not isinstance(delay, int) or delay < 0 or tau < 0 or seconds < 60:
        raise ValueError('invalid simulation timing')
    device, clock = controller(gains, initial, target, pwm=pwm, minimum=minimum)
    temperature, filtered = initial, 0.0
    inputs = deque([0.0] * delay)
    temperatures = []
    maximum_integral = 0.0
    for t in range(seconds + 1):
        clock.now = t
        if t % 30 == 0:
            device._current_temp = round(temperature + noise * sin(t / 37), 2)
            device._previous_temp_time = device._cur_temp_time
            device._cur_temp_time = t
            await device._async_control_heating(calc_pid=True)
            temperatures.append((t, temperature))
            maximum_integral = max(maximum_integral, device._pid_controller.integral)
        if t == seconds:
            break
        inputs.append(float(device._is_device_active))
        delivered = inputs.popleft()
        filtered = delivered if tau == 0 else delivered + (filtered - delivered) * exp(-1 / tau)
        temperature += (rate * filtered + drift) / 3600
    steady = [abs(value - target) for t, value in temperatures if t >= seconds * 2 / 3]
    durations = [(on, b - a) for (a, on), (b, _) in zip(device.transitions, device.transitions[1:])]
    return {
        'overshoot_c': max(0, max(v for _, v in temperatures) - target),
        'last_third_mae_c': sum(steady) / len(steady),
        'whole_run_mae_c': sum(abs(v - target) for _, v in temperatures) / len(temperatures),
        'first_target_crossing_seconds': next((t for t, v in temperatures if v >= target), None),
        'maximum_integral_output': maximum_integral,
        'final_temperature': temperature,
        'heat_starts': sum(on for _, on in device.transitions),
        'shortest_completed_on_seconds': min((d for on, d in durations if on), default=None),
        'shortest_completed_off_seconds': min((d for on, d in durations if not on), default=None),
        'transitions': device.transitions,
        'simulated_only': True,
    }
