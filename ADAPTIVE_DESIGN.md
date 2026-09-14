# Broader PID adaptation: implementation contract

## Source

Adaptive Climate commit 0d32c1fdf06a86bdabe2c5ee533b14df0fba78f1:
https://github.com/afewyards/ha-adaptive-climate/tree/0d32c1fdf06a86bdabe2c5ee533b14df0fba78f1

`adaptive_pid_rules.py` is its `adaptive/pid_rules.py`, with only relative
constant imports redirected. The required constants retain upstream values.
The original MIT notice is in ADAPTIVE_CLIMATE_LICENSE.

`adaptive_cycle_analysis.py` is an unchanged copy of the upstream cycle-analysis
module. `pid_cycle_metrics.py` invokes those functions following the upstream
manager's distinction between cold recovery and post-heating undershoot.

`adaptive_robust_stats.py` is an unchanged copy of upstream `adaptive/robust_stats.py`.
The proposal adapter now uses its median/MAD summary as `learning_adjustments.py`
does, rather than an arithmetic mean. A normalized-content hash test records
the copied source. Validation deliberately retains the conservative arithmetic
mean so a large post-change degradation is not filtered out.

## Traceability

| Behavior | Upstream path | Local status |
| --- | --- | --- |
| Overshoot, undershoot, response, settling, oscillation rules | adaptive/pid_rules.py | Actual evaluator copied with attribution |
| Conflicting rule priority | adaptive/pid_rules.py | Actual resolver copied |
| Ignore ordinary PWM oscillations | adaptive/learning_coordinator.py | Implemented in proposal adapter |
| Six qualifying cycles; 36 hours and eight new cycles between changes | const.py AUTO_APPLY_THRESHOLDS forced_air | Implemented in proposal adapter |
| Separate recommendation from applying gains | managers/pid_tuning.py | Explicit propose/committed interface |
| Five-cycle validation, 30 percent degradation check | adaptive/validation.py and const.py | Adapter tests include overshoot and undershoot |
| Persist gain history and rollback | managers/state_restorer.py, pid_tuning.py | Thermostat wiring and durable transaction tested with fake HA dependencies; real HA validation pending |
| Initialize PID | adaptive/physics.py (comparison only) | Local history model and SIMC candidate calculator implemented; acceptance/application pending |
| Cycle quality, confidence, convergence | adaptive/learning_coordinator.py and cycle analysis | Full metric acquisition and confidence gates still pending |
| Robust proposal summaries | adaptive/robust_stats.py and learning_adjustments.py | Copied upstream statistics and wired into proposals |

## Deliberate differences, not claimed as copied upstream behavior

- Last 14 days of Recorder data is the user's requested initialization source.
- No permanent lifetime adjustment limit: learning must continue indefinitely.
- The provisional adapter limits a single proposal to +/-20 percent per gain.
  This is a local trust bound and needs validation, not a copied upstream rule.
- Validation checks both overshoot and undershoot, so reduced overshoot caused
  by underheating is not accepted as success.
- Proposal records are chronological and unique by cycle start. Conflicting
  duplicates or a disturbed duplicate with the same start withhold that cycle.
  Missing, nonfinite, Boolean, negative, or malformed measurements are rejected;
  this is data validation, not a claim that environmental disturbances can all
  be inferred or that confidence/convergence learning is complete.
- Gain bounds must be provided in the host PID's units. They are not guessed.
- A zero Ki or Kd explicitly blocks multiplicative adjustment until genuine
  initialization exists. Zero times a multiplier must not be labeled learning.
- `initial_pid.py` now supplies a pure initial-candidate calculator using the
  published SIMC integrating-with-lag rule and series-to-parallel conversion.
  This is a separate, attributed method, not a port of reference physics gains.
  It remains disconnected from automatic gain application pending model and
  closed-loop validation. Design response time is explicit and provisional.

## Compatibility work required

The installed Smart Thermostat computes Ki * error * seconds and derivative
Kd * temperature_delta / seconds. Adaptive Climate uses hours for I/D and
P-on-measurement rather than the installed proportional-on-error controller.
Therefore its numeric gains, physics initialization and limits cannot be
transplanted blindly. This comparison must precede automatic application.

The observer now captures temperature samples and runs upstream overshoot,
undershoot, rise-time, settling-time, oscillation and settling-error functions.
The regulation-cycle boundary handling still needs review against upstream.
Missing metrics block proposals rather than being substituted with fabricated
zero values. Recorder and live observations retain which gains produced each
cycle; changes to gains during a cycle invalidate that sample.

Demand-session measurements now distinguish nonzero controller demand from
physical PWM pulses, following managers/heater_cycle_bookkeeper.py and
heater_controller.py. Runtime accumulates actual relay-on intervals; final
physical off is the reference for coast/settling. Recorded control_output is
required for history to qualify as session data. Legacy pulse records remain
observable but cannot drive automatic gain changes. The reference's demand-zero
debounce now waits two recorded PWM periods before finalizing a session, without
affecting actuator control. Automatic updates require a recorded PWM period
matching the current configuration. Unknown historical timing is not inferred.
Low-output maintenance/session timeout policies are not yet ported.
Early reheating still withholds an incomplete sample rather than finalizing it
as a clean cycle. This is a deliberate conservative difference pending review.

The I/D time-unit conversion is implemented and tested against equivalent term
outputs at 5, 30, 60, 300 and 3600-second sample intervals. It deliberately does
not claim to convert P-on-measurement into P-on-error.

`thermal_response.py` is local history-calibration groundwork, not copied
reference physics initialization. It records temperature changes over exact
physical on intervals and separately measures post-off rise/peak timing. It
does not treat net temperature rise as furnace power or infer building heat
loss. Replay reports observations only and no initial gains. This distinction
matters when recorded temperature rises mostly after the heater has stopped.

Pure validation-state restoration retains cooldown and completed validation
cycles, rejects corrupt/future state or mismatched current gains, and retains a
rollback decision until acknowledged by the gain owner. These are local tests,
not a claim of completed live Home Assistant integration.

## Current deployment boundary

The old predictive cutoff is disconnected from climate.py. Its standalone
module/tests remain only as a superseded experiment, not an active control path.
The PID proposal/validation engine is now wired into the local thermostat at
heat-off boundaries. Durable old/new gain intent is saved before assignment,
and current gains, mode, target and eligibility are rechecked after that IO.
Previous gains are retained for rollback after five worse validation cycles.
Recovering a saved transaction never overwrites manually changed gains.

The I/D rule bounds are converted from upstream hourly units. P bounds remain
the upstream numeric limits, not a conversion of the P-on-M formulation or a
claim of equivalent physical calibration. Historical zero-gain initialization,
confidence/quality completion and regulation-cycle boundary review remain
deployment blockers. The user-specific zero Ki/Kd case still withholds tuning.

Tests exercise the actual thermostat gain methods using fake HA state/storage
objects, including rollback, manual-off races, first/second storage failures,
and sensor validity. They are not live furnace tests.
No live HA files, configuration, thermostat values or services were changed.
