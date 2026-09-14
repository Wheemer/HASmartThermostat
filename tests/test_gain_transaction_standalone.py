"""Failure and manual-change tests for gain application, without Home Assistant."""

from copy import deepcopy
import importlib
import unittest
from test_history_learning_standalone import PACKAGE
from test_pid_adaptation_standalone import Adaptation

module = importlib.import_module(PACKAGE + '.gain_transaction')


class TransactionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.gains = {'kp': 100, 'ki': 0.01, 'kd': 100}
        self.context = dict(gains=self.gains, target=22, mode='heat', eligible=True)
        self.engine = Adaptation({'kp': (10, 500), 'ki': (0, 1), 'kd': (0, 12000)})
        self.proposal = dict(old=self.gains, new=self.gains | {'kd': 120},
                             baseline=dict(overshoot=0.4, undershoot=0, oscillations=0,
                                           rise_time=3, settling_time=5))
        self.writes = []
        self.applied = []

    async def save(self, journal, learning):
        self.writes.append(deepcopy((journal, learning)))

    def apply(self, gains):
        self.applied.append(gains)
        self.context['gains'] = gains

    async def run_change(self, save=None):
        return await module.commit_gain_change(self.proposal, self.engine, 2000,
                                               lambda: self.context, save or self.save, self.apply)

    async def test_intent_precedes_gain_assignment(self):
        async def save(journal, learning):
            if journal is not None:
                self.assertEqual(self.applied, [])
            await self.save(journal, learning)
        self.assertEqual(await self.run_change(save), 'applied')
        self.assertEqual(len(self.writes), 2)
        self.assertIsNone(self.writes[-1][0])
        self.assertEqual(self.context['target'], 22)
        self.assertEqual(self.context['mode'], 'heat')

    async def test_failed_first_write_never_changes_gains(self):
        async def fail(*args):
            raise OSError('storage unavailable')
        with self.assertRaises(OSError):
            await self.run_change(fail)
        self.assertEqual(self.applied, [])
        self.assertIsNone(self.engine.pending)

    async def test_manual_off_during_save_wins(self):
        async def save(journal, learning):
            await self.save(journal, learning)
            self.context['mode'] = 'off'
        self.assertEqual(await self.run_change(save), 'deferred')
        self.assertEqual(self.applied, [])

    async def test_manual_target_change_during_save_wins(self):
        async def save(journal, learning):
            await self.save(journal, learning)
            self.context['target'] = 21
        self.assertEqual(await self.run_change(save), 'deferred')
        self.assertEqual(self.applied, [])

    async def test_second_write_failure_keeps_recoverable_journal(self):
        async def save(journal, learning):
            if journal is None:
                raise OSError('storage unavailable')
            await self.save(journal, learning)
        with self.assertRaises(OSError):
            await self.run_change(save)
        journal = self.writes[0][0]
        self.assertEqual(module.recover_gain_change(journal, self.proposal['new']), self.engine.snapshot())
        self.assertIsNone(module.recover_gain_change(journal, self.proposal['old'])['pending'])

    async def test_unrecognized_restored_gains_are_not_overwritten(self):
        await self.run_change()
        with self.assertRaises(ValueError):
            module.recover_gain_change(self.writes[0][0], self.gains | {'kp': 99})


if __name__ == '__main__':
    unittest.main()
