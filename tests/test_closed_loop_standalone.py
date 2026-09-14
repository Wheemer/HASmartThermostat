"""Existing output semantics exercised without hardware or HA service access."""

import unittest
from closed_loop_lab import controller, simulate
from test_initial_pid_standalone import calculate


class ClosedLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_full_demand_cannot_restart_before_minimum_off(self):
        device, clock = controller(calculate()['gains'], 20, 22)
        device._last_heat_cycle_time = 0
        device._control_output = 100
        clock.now = 149
        await device.set_control_value()
        self.assertFalse(device._is_device_active)
        clock.now = 150
        await device.set_control_value()
        self.assertTrue(device._is_device_active)

    async def test_zero_demand_respects_minimum_on_but_explicit_off_can_stop(self):
        device, clock = controller(calculate()['gains'], 20, 22)
        device._control_output = 100
        await device.set_control_value()
        clock.now = 30
        device._control_output = 0
        await device.set_control_value()
        self.assertTrue(device._is_device_active)
        device._hvac_mode = 'off'
        await device._async_control_heating()
        self.assertFalse(device._is_device_active)

    async def test_same_second_output_reversal_respects_minimum_cycle(self):
        device, clock = controller(calculate()['gains'], 20, 22)
        device._control_output = 100
        await device.set_control_value()
        self.assertTrue(device._is_device_active)

        device._control_output = 0
        await device.set_control_value()
        self.assertTrue(device._is_device_active)
        self.assertNotIn((clock.now, False), device.transitions)

    async def test_rejected_pwm_turn_on_does_not_reset_cycle_timer_or_force(self):
        device, clock = controller(calculate()['gains'], 20, 22)
        device._last_heat_cycle_time = 0
        device._time_changed = -900
        device._control_output = 70
        device._force_on = True
        clock.now = 30

        await device.set_control_value()
        self.assertFalse(device._is_device_active)
        self.assertEqual(device._time_changed, -900)
        self.assertTrue(device._force_on)

    async def test_cooler_duty_increases_as_room_gets_hotter(self):
        gains = {'kp': 10.0, 'ki': 0.0, 'kd': 0.0}
        far, far_clock = controller(gains, 27, 22, mode='cool')
        near, near_clock = controller(gains, 22.5, 22, mode='cool')

        for device, clock in ((far, far_clock), (near, near_clock)):
            device._previous_temp_time = 0
            device._cur_temp_time = 60
            clock.now = 60
            await device._async_control_heating(calc_pid=True)

        self.assertLess(far._control_output, near._control_output)
        self.assertGreater(abs(far._control_output), abs(near._control_output))

    async def test_leaving_saturated_output_does_not_immediately_turn_off(self):
        device, clock = controller(calculate()['gains'], 20, 22)
        device._control_output = 100
        await device.set_control_value()
        self.assertTrue(device._is_device_active)

        clock.now = 1200
        await device.set_control_value()
        self.assertEqual(device._time_changed, 1200)

        device._control_output = 80
        clock.now = 1201
        await device.set_control_value()
        self.assertTrue(device._is_device_active)
        self.assertNotIn((1201, False), device.transitions)

    async def test_closed_loop_has_real_transitions_and_obeys_minimums(self):
        result = await simulate(calculate()['gains'], seconds=7200)
        self.assertGreater(result['heat_starts'], 0)
        self.assertGreaterEqual(result['shortest_completed_on_seconds'], 150)
        self.assertGreaterEqual(result['shortest_completed_off_seconds'], 150)
        self.assertTrue(result['simulated_only'])

    async def test_simulation_is_deterministic_without_real_time_or_services(self):
        first = await simulate(calculate()['gains'], seconds=3600, noise=0.02)
        second = await simulate(calculate()['gains'], seconds=3600, noise=0.02)
        self.assertEqual(first, second)
