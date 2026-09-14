"""Demonstrate exact compatibility differences before adapting reference code."""

import unittest
from reference_adaptive_lab import ReferencePID, physics
from test_initial_pid_standalone import PID as HostPID


def pair():
    reference = ReferencePID(1.2, 8, 0.8, out_min=0, out_max=100,
                             derivative_filter_alpha=1, heating_type='forced_air')
    host = HostPID(1.2, 8 / 3600, 0.8 * 3600, out_min=0, out_max=100)
    return reference, host


class ReferenceCompatibilityTests(unittest.TestCase):
    def test_forced_air_initialization_uses_actual_reference_profiles(self):
        self.assertEqual(physics.calculate_initial_pid(0.5, 'forced_air'), (1.8, 12.0, 0.4))
        self.assertEqual(physics.calculate_initial_pid(1.5, 'forced_air'), (1.2, 8.0, 0.8))
        self.assertEqual(physics.calculate_initial_pid(3.0, 'forced_air'), (0.85, 5.5, 1.3))

    def test_unit_conversion_matches_unfiltered_id_terms_in_shared_operating_region(self):
        reference, host = pair()
        for pid in (reference, host):
            pid.integral = 10.0
            pid.calc(20, 20.5, input_time=0)
            pid.calc(20.1, 20.5, input_time=30, last_input_time=0)
        self.assertAlmostEqual(reference.integral, host.integral)
        self.assertAlmostEqual(reference.derivative, host.derivative)
        self.assertNotAlmostEqual(reference.proportional, host.proportional)

    def test_proportional_behavior_cannot_be_fixed_by_unit_conversion(self):
        reference, host = pair()
        reference.calc(20, 21, input_time=0)
        host.calc(20, 21, input_time=0)
        self.assertEqual(reference.proportional, 0)
        self.assertAlmostEqual(host.proportional, 1.2)
        reference.calc(20, 22, input_time=30, last_input_time=0)
        host.calc(20, 22, input_time=30, last_input_time=0)
        self.assertEqual(reference.proportional, 0)
        self.assertAlmostEqual(host.proportional, 2.4)

    def test_reference_can_accumulate_heating_demand_from_zero_output(self):
        reference = ReferencePID(0, 8, 0, out_min=0, out_max=100, heating_type='forced_air')
        host = HostPID(0, 8 / 3600, 0, out_min=0, out_max=100)
        for pid in (reference, host):
            pid.calc(20, 21, input_time=0)
            pid.calc(20, 21, input_time=30, last_input_time=0)
        self.assertGreater(reference.integral, 0)
        self.assertEqual(host.integral, 0)

    def test_reference_derivative_filter_is_part_of_effective_response(self):
        reference = ReferencePID(0, 0, 0.8, out_min=0, out_max=100)
        host = HostPID(0, 0, 0.8 * 3600, out_min=0, out_max=100)
        for pid in (reference, host):
            pid.calc(20, 21, input_time=0)
            pid.calc(20.1, 21, input_time=30, last_input_time=0)
        self.assertAlmostEqual(reference.derivative, host.derivative * 0.15)

    def test_reference_freezes_id_on_rapid_calls(self):
        reference, host = pair()
        for pid in (reference, host):
            pid.integral = 10.0
            pid.calc(20, 21, input_time=0)
            pid.calc(20.1, 21, input_time=1, last_input_time=0)
        self.assertEqual(reference.derivative, 0)
        self.assertAlmostEqual(reference.integral, 10, places=2)
        self.assertNotEqual(host.derivative, 0)

    def test_reference_tolerance_clamp_does_not_match_host_raw_output(self):
        reference, host = pair()
        for pid in (reference, host):
            pid.integral = 10.0
        reference_output, _ = reference.calc(22, 21, input_time=0)
        host_output, _ = host.calc(22, 21, input_time=0)
        self.assertEqual(reference_output, 0)
        self.assertGreater(host_output, 0)
