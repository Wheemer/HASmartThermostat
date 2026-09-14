"""Chronological whole-day checks for the offline model, never PID approval."""

from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

from thermal_model_lab import fit_response_model


def validate_by_day(windows, *, timezone, delays, time_constants):
    zone = ZoneInfo(timezone)
    days = defaultdict(list)
    ordered = sorted(windows, key=lambda w: w['samples'][0][0])
    for window in ordered:
        # A pulse crossing midnight belongs to its completion day; otherwise
        # training for the next day could already contain that day's response.
        day = datetime.fromtimestamp(window['samples'][-1][0], zone).date().isoformat()
        days[day].append(window)
    earlier, folds, skipped = [], [], []
    for day, later in sorted(days.items()):
        if len(earlier) >= 2 and len(later) >= 2:
            result = fit_response_model(earlier, later, delays=delays, time_constants=time_constants)
            folds.append({'day': day, 'training_windows': len(earlier),
                          'validation_windows_count': len(later), 'result': result})
        else:
            skipped.append({'day': day, 'reason': 'insufficient_training_or_validation_windows'})
        earlier.extend(later)
    successful = [f['result'] for f in folds if f['result']['status'] == 'experimental_model_only']
    ranges = {}
    for key in ('delay_seconds', 'response_time_seconds', 'heat_rate_c_per_hour',
                'background_drift_c_per_hour'):
        values = [r[key] for r in successful]
        ranges[key] = {'min': min(values), 'max': max(values)} if values else None
    return {
        'status': 'not_ready_for_gain_synthesis',
        'timezone': timezone,
        'context_ids_complete': bool(ordered) and all(w.get('context_ids_complete') is True for w in ordered),
        'folds': folds, 'skipped_days': skipped, 'fitted_parameter_ranges': ranges,
        'search_boundary_folds': sum(r['on_search_boundary'] for r in successful),
        'folds_with_any_window_worse_than_drift': sum(not r['every_window_beats_drift_only'] for r in successful),
        'initial_gains': None,
    }
