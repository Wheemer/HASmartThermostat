"""Bounded offline gain experiments. This is not the integration's autotuner.

The factor grid is an explicitly local exploration around the published seed.
It does not relax the regression gate or convert simulation results into live
settings. Changing gains independently tests whether the SIMC ratio constraint
is the problem under discrete PWM. Derivative-free candidates are included.
"""

import argparse
import asyncio
import itertools
import json
from math import isfinite
from pathlib import Path

from closed_loop_lab import simulate
from gain_validation_lab import compare_results
from test_initial_pid_standalone import calculate


SCENARIOS = {
    'nominal': {}, 'cold_start': {'initial': 20}, 'longer_delay': {'delay': 600},
    'stronger_heat': {'rate': 9}, 'sensor_noise': {'noise': 0.03}, 'higher_loss': {'drift': -0.5},
}


def candidates(seed, kp_factors, ki_factors, kd_factors):
    grids = (kp_factors, ki_factors, kd_factors)
    if any(not values for values in grids):
        raise ValueError('factor grids cannot be empty')
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not isfinite(v) or v < 0
           for values in grids for v in values):
        raise ValueError('invalid gain factor')
    if len(kp_factors) * len(ki_factors) * len(kd_factors) > 256:
        raise ValueError('offline search budget exceeded')
    for p, i, d in itertools.product(sorted(set(kp_factors)), sorted(set(ki_factors)), sorted(set(kd_factors))):
        if p == 0 or i == 0:
            continue
        yield {'kp': seed['kp'] * p, 'ki': seed['ki'] * i, 'kd': seed['kd'] * d}


async def search(seed, *, kp_factors=(0.5, 1, 2), ki_factors=(0.05, 0.1, 0.25, 0.5),
                 kd_factors=(0, 0.5, 1, 2), runner=simulate):
    grid = list(candidates(seed, kp_factors, ki_factors, kd_factors))
    baseline_gains = {'kp': 100, 'ki': 0, 'kd': 0}
    baseline = {name: await runner(baseline_gains, **options) for name, options in SCENARIOS.items()}
    trials = []
    for gains in grid:
        results = {name: await runner(gains, **options) for name, options in SCENARIOS.items()}
        assessment = compare_results(baseline, results)
        trials.append({'gains': gains, 'assessment': assessment, 'results': results})
    return {
        'status': 'offline_search_only', 'apply': False,
        'seed': seed, 'baseline_gains': baseline_gains, 'baseline': baseline,
        'trial_count': len(trials),
        'eligible_count': sum(t['assessment']['status'] == 'eligible_for_further_validation' for t in trials),
        'trials': trials,
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(search(calculate(heat_rate_c_per_hour=6.454022859370919)['gains']))
    args.output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps({k: result[k] for k in ('status', 'trial_count', 'eligible_count', 'apply')}))
