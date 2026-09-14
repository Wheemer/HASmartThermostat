"""Demand-session boundaries are distinct from physical PWM pulses."""

import unittest

from test_history_learning_standalone import Observer, replay, row

GAINS = {'kp': 100, 'ki': 0.01, 'kd': 100}


def temperature(t):
    if t < 660:
        return 21 + t / 660
    if t < 780:
        return 22 + (t - 660) / 400
    return 22.3 - (t - 780) / 1000


class SessionTests(unittest.TestCase):
    def test_replay_preserves_known_pwm_and_applies_debounce(self):
        data = {
            'sensor.temp': [row(t, str(21 + t / 1000)) for t in range(0, 2101, 30)],
            'climate.heat': [row(t, 'heat', temperature=22, pid_mode='auto',
                                control_output=output, learning_pwm_seconds=60, **GAINS)
                             for t, output in [(0, 0), (60, 50), (240, 0), (300, 50), (480, 0)]],
            'switch.heat': [row(t, value) for t, value in
                            [(0, 'off'), (60, 'on'), (240, 'off'), (300, 'on'), (480, 'off')]],
        }
        snapshot, report = replay(data, 'sensor.temp', 'climate.heat', ['switch.heat'], 0, 2100)
        self.assertEqual(report['timed_demand_sessions'], 1)
        self.assertEqual(snapshot['records'][0]['runtime_seconds'], 360)
        self.assertEqual(snapshot['records'][0]['pwm_seconds'], 60)

    def test_merge_prefers_timed_session_regardless_of_input_order(self):
        observer = Observer(settle_seconds=0)
        for t in range(0, 631, 30):
            observer.sample(t, 21 + t / 1000, 22, 60 <= t < 240,
                            gains=GAINS, demand=60 <= t < 240, pwm_seconds=60)
        timed = dict(observer.records[0])
        untimed = {**timed, 'pwm_seconds': None}
        for saved, imported in [(timed, untimed), (untimed, timed)]:
            observer.records = [saved]
            observer.merge_history([imported], 0)
            self.assertEqual(len(observer.records), 1)
            self.assertEqual(observer.records[0]['pwm_seconds'], 60)

    def test_zero_pwm_has_no_measurement_debounce(self):
        observer = Observer()
        for t in range(0, 241, 30):
            observer.sample(t, 21 + t / 1000, 22, 60 <= t < 240,
                            gains=GAINS, demand=60 <= t < 240, pwm_seconds=0)
        self.assertEqual(observer.status, 'settling')

    def test_zero_demand_must_last_two_pwm_periods(self):
        observer = Observer()
        for t in range(0, 361, 30):
            observer.sample(t, 21 + t / 1000, 22, 60 <= t < 240,
                            gains=GAINS, demand=60 <= t < 240, pwm_seconds=60)
            if 240 <= t < 360:
                self.assertEqual(observer.status, 'heating')
                self.assertEqual(observer.records, [])
        self.assertEqual(observer.status, 'settling')
        self.assertEqual(observer._cycle['stop'], 240)

    def test_brief_zero_demand_resets_debounce_and_keeps_one_session(self):
        observer = Observer(settle_seconds=0)
        for t in range(0, 631, 30):
            on = 60 <= t < 240 or 300 <= t < 480
            observer.sample(t, 21 + t / 1000, 22, on,
                            gains=GAINS, demand=on, pwm_seconds=60)
            if 240 <= t < 600:
                self.assertEqual(observer.status, 'heating')
        self.assertEqual(len(observer.records), 1)
        self.assertEqual(observer.records[0]['runtime_seconds'], 360)
        self.assertEqual(observer.records[0]['pwm_seconds'], 60)
        self.assertEqual(observer.diagnostics()['timed_demand_sessions'], 1)

    def test_pwm_change_invalidates_in_progress_session(self):
        observer = Observer()
        observer.sample(0, 21, 22, False, gains=GAINS, demand=False, pwm_seconds=60)
        observer.sample(60, 21, 22, True, gains=GAINS, demand=True, pwm_seconds=60)
        observer.sample(120, 21.1, 22, True, gains=GAINS, demand=True, pwm_seconds=900)
        self.assertEqual(observer.reason, 'pwm_period_changed')
        self.assertIsNone(observer._cycle)

    def test_invalid_pwm_values_are_not_timing_evidence(self):
        for period in (True, -1, float('nan'), float('inf'), '900'):
            observer = Observer()
            observer.sample(0, 21, 22, False, demand=False, pwm_seconds=period)
            self.assertEqual(observer.reason, 'invalid_pwm_period')

    def test_two_pwm_pulses_are_one_session_with_actual_on_time(self):
        observer = Observer()
        for t in range(0, 1021, 30):
            observer.sample(t, temperature(t), 22,
                            60 <= t < 240 or 480 <= t < 660, gains=GAINS,
                            demand=60 <= t < 720)
            if 240 <= t < 480:
                self.assertEqual(observer.status, 'heating')
                self.assertEqual(observer.records, [])
        self.assertEqual(len(observer.records), 1)
        result = observer.records[0]
        self.assertEqual(result['cycle_basis'], 'demand_session')
        self.assertEqual(result['started'], 60)
        self.assertEqual(result['stopped'], 660)
        self.assertEqual(result['runtime_seconds'], 360)
        self.assertAlmostEqual(result['coast'], 0.3)

    def test_no_settling_until_demand_and_actual_heat_both_stop(self):
        observer = Observer()
        for t in range(0, 361, 30):
            observer.sample(t, 21 + t / 500, 22, 60 <= t < 360,
                            gains=GAINS, demand=60 <= t < 240)
            if 240 <= t < 360:
                self.assertEqual(observer.status, 'heating')
        self.assertEqual(observer.status, 'settling')
        self.assertEqual(observer._cycle['stop'], 360)

    def test_initial_on_or_initial_positive_demand_is_not_a_complete_start(self):
        observer = Observer()
        for t in range(0, 601, 30):
            observer.sample(t, 21, 22, 120 <= t < 300, gains=GAINS, demand=t < 360)
        self.assertIsNone(observer._cycle)
        self.assertEqual(observer.records, [])

    def test_loss_of_demand_telemetry_invalidates_session(self):
        observer = Observer()
        observer.sample(0, 21, 22, False, gains=GAINS, demand=False)
        observer.sample(60, 21, 22, True, gains=GAINS, demand=True)
        observer.sample(120, 21.1, 22, True, gains=GAINS, demand=None)
        self.assertEqual(observer.reason, 'demand_basis_changed')
        self.assertIsNone(observer._cycle)

    def test_incomplete_settling_remains_withheld_when_demand_returns(self):
        observer = Observer()
        for t in range(0, 361, 30):
            observer.sample(t, 21 + t / 1000, 22, 60 <= t < 240,
                            gains=GAINS, demand=60 <= t < 240 or t >= 360)
        self.assertEqual(observer.reason, 'reheated_before_settling')
        self.assertEqual(observer.records, [])

    def test_replay_uses_recorded_output_not_relay_as_demand(self):
        data = {
            'sensor.temp': [row(t, str(temperature(t))) for t in range(0, 1021, 30)],
            'climate.heat': [row(t, 'heat', temperature=22, pid_mode='auto', control_output=output, **GAINS)
                             for t, output in [(0, 0), (60, 50), (720, 0)]],
            'switch.heat': [row(t, value) for t, value in
                            [(0, 'off'), (60, 'on'), (240, 'off'), (480, 'on'), (660, 'off')]],
        }
        snapshot, report = replay(data, 'sensor.temp', 'climate.heat', ['switch.heat'], 0, 1020)
        self.assertEqual(report['completed_cycles'], 1)
        self.assertEqual(snapshot['records'][0]['runtime_seconds'], 360)
        self.assertEqual(snapshot['records'][0]['cycle_basis'], 'demand_session')

    def test_completed_session_survives_restore_incomplete_one_does_not(self):
        observer = Observer()
        for t in range(0, 1021, 30):
            observer.sample(t, temperature(t), 22, 60 <= t < 660,
                            gains=GAINS, demand=60 <= t < 720)
        restored = Observer()
        restored.restore(observer.snapshot())
        self.assertEqual(restored.records[0]['cycle_basis'], 'demand_session')
        self.assertEqual(restored.records[0]['runtime_seconds'], 600)
        self.assertIsNone(restored._cycle)
        self.assertFalse(restored._armed)

    def test_replayed_session_can_replace_nearby_legacy_pulse(self):
        observer = Observer()
        for t in range(0, 1021, 30):
            observer.sample(t, temperature(t), 22, 60 <= t < 660,
                            gains=GAINS, demand=60 <= t < 720)
        session = dict(observer.records[0])
        observer.records = [{**session, 'cycle_basis': 'relay_pulse', 'stopped': 659}]
        observer.merge_history([session], 0)
        self.assertEqual(len(observer.records), 1)
        self.assertEqual(observer.records[0]['cycle_basis'], 'demand_session')
        self.assertEqual(observer.diagnostics()['demand_sessions'], 1)
