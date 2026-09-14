# Adaptive heating development

## Current direction supersedes the prototype below

The user requires broader PID adaptation, not predictive cutoff over fixed
gains. The cutoff has been removed from the active climate control path.
See ADAPTIVE_DESIGN.md for the current implementation and source mapping.
There are 217 passing standalone tests, including actual gain application and
rollback methods against fake HA objects, durable gain transactions, PID adaptation, upstream cycle
metrics, I/D unit equivalence, gain provenance, saved validation state and
compatibility with the installed preset/service/actuator methods;
the count also includes the retained, inactive cutoff experiment's tests.
Local gain application is wired. Regulation-cycle boundary review, confidence
gates, history-based initialization and real HA runtime validation are not
finished. This is not ready for deployment.

## UI configuration and lifecycle (local, September 10)

- Config entries now load the original thermostat class and entity services.
- The YAML platform schedules a one-time import instead of creating a second
  thermostat. Existing unique IDs are retained; missing IDs use the original
  name/heater slug algorithm. Already imported YAML cannot overwrite UI edits.
- Imported validated YAML is retained separately in entry data. Source YAML is
  not edited or deleted by the integration. Back up and remove only successfully
  migrated thermostat blocks during the eventual supervised deployment.
- Native UI selectors expose every integration-specific YAML setting except the
  immutable unique ID, in controller, temperature, preset, timing, PID/learning,
  and output sections. Options edits reload the config entry.
- Current preset temperatures are shown in the preset form when runtime state
  exists. Initial import retains restored runtime values. Explicit UI changes
  take precedence on reload; unrelated edits do not reset learned gains or
  helper-synchronized presets. Configured gains remain separate from live gains.
- All async control, output, and service entrypoints have an entity-owned task
  guard. Unloading closes the old instance, cancels/drains active operations,
  cancels history import, saves learning, and always invokes base cleanup even
  if persistence fails. Late callbacks are gated. New instances have fresh guards.
- An already dispatched external service/device command cannot be retracted by
  unloading. The guard prevents subsequent commands from the removed entity;
  it is not a hardware acknowledgement guarantee.
- Tests cover migration serialization, complete field/translation coverage,
  import deduplication, optional removal, state restoration, lifecycle forwarding,
  active-task cancellation, nested work, late calls, replacement instances and
  persistence failure cleanup. Flow/registry/control dependencies are fakes,
  not a real Home Assistant installation or rendered UI validation.
- Real HA runtime results are recorded below. Rendered UI verification and
  actual furnace/control-model validation remain outstanding. No deployment,
  live config edits, or live HA restart occurred.

## Isolated Home Assistant runtime validation

`tests/ha_runtime_smoke.py` passed in the locally cached HA container:
Home Assistant 2026.7.2, Python 3.14.6. Docker ran with `--network none`, the
repository mounted read-only, disposable configuration directories, and an
input Boolean as a simulated heater. No live HA config or devices were mounted.

Verified against actual HA APIs, beyond the standalone tests:

- First YAML import through full HA bootstrap creates one config entry.
- Migration adopts an existing entity registry ID and attaches it to the entry.
- Fractional timing (2.5 minutes) survives as 150 seconds.
- Existing thermostat entity services remain registered.
- All six options schemas serialize using HA's real selector serialization.
- Editing Sleep via the options flow reloads the entry and applies the value.
- Duplicate YAML import cannot overwrite the UI configuration.
- Unload/setup retains the same entity ID, Sleep value, and off mode.
- An injected stalled control operation reached through a real HA entity service
  is cancelled by entry unload. The old entity rejects later HVAC commands.
- A deliberately stalled history importer does not block HA task wrap-up and
  is cancelled on unload. This tests lifecycle, not actual Recorder retrieval.
- Full HA stop/start from the same temporary configuration restores the UI-only
  entry, entity ID, edited Sleep value, and off state without thermostat YAML.

Runtime caveats: isolated analytics setup logs a Zeroconf network-interface error
because networking is disabled. Fresh test entities have no previous setpoint
and use the existing minimum-temperature default. This is not a test of real
furnace response or automatic calibration.

## Output availability and startup ordering

The real-runtime test initially exposed off commands issued before the output
platform had loaded. This is now guarded locally:

- Missing, unknown, unavailable, or restored-placeholder outputs receive no
  commands. Skipped on/off operations do not advance the minimum-cycle clock.
- On waits for all selected outputs to be available. Off can still stop an
  available output when a different output is unavailable. Valve/number/light
  writes also skip unavailable targets.
- An unavailable-to-available transition schedules one coalesced normal control
  evaluation after thermostat restoration. No polling/retry loop was added.
  Ordinary on/off transitions do not become a new reconciliation trigger.
- Removal cancels the queued reconciliation task, including before its coroutine
  has started, as well as the existing active-operation and history cleanup.
- Standalone tests cover availability, inverted polarity, minimum-cycle timing,
  partial output availability, event coalescing, and removal before execution.
- The real HA test simulates an unavailable output returning ON while the
  thermostat is OFF. Normal control returns it OFF without waiting 60 seconds.
- The final HA container run passes a log assertion for no thermostat errors
  and no unsupported test-heater commands across initial boot, reload, and
  restart. The intentionally offline analytics error is separate and remains.

## Latest learning-data hardening

- Copied the reference integration's robust median/MAD statistics unchanged,
  with its existing license attribution, and used them for gain proposals.
- Proposals use chronological recent cycles, not input-list order. Duplicate
  cycles cannot satisfy the sample minimum; contradictory or disturbed copies
  withhold the affected cycle.
- Metrics and timestamps reject Boolean, negative, nonfinite, incomplete, and
  malformed values. The same validation also protects post-change validation
  and restoration of saved validation cycles.
- Added tests for isolated overshoot outliers, chronology, duplicates/conflicts,
  malformed data, and conservative rollback. Post-change validation does not
  filter away a large worsening.
- This is not zero-I/D initialization. That calibration, confidence/convergence
  completion and regulation-cycle boundary review remain deployment blockers.

## Demand-session measurements

- The observer accepts actual controller demand independently of the relay
  state, following the reference heater bookkeeper's session/pulse distinction.
  PWM off intervals do not end a session while demand remains positive.
- Actual on-time is accumulated across pulses. Coast and settling metrics use
  the final physical off timestamp, even if demand reaches zero later.
- Live observation receives control_output demand; Recorder replay uses the
  recorded control_output attribute. Neither is inferred from temperature error.
- Missing demand remains explicitly relay-pulse data. The automatic gain
  application path excludes those old/unknown-basis records; replayed sessions
  can replace matching legacy pulse records. Diagnostics count sessions separately.
- Initial-on windows, missing demand mid-session, and renewed demand before
  settling invalidate incomplete observations. Only completed sessions persist.
- Tests cover multiple PWM pulses, cumulative runtime, demand/actual-off ordering,
  missing demand, history replay, restoration, and excluding legacy measurements.
- Replaying the existing private history sample yielded 10 candidate demand
  sessions. This is not a new live export, complete 14-day validation, proof of
  no manual disturbances, or identification of suitable initial PID gains.
- Still to review against upstream: low-output maintenance
  session endings, and data-driven settling limits. The present conservative
  handling withholds incomplete settling rather than treating it as good data.
- No thermostat command, preset, target, minimum-cycle, or live configuration
  changes were made by this measurement work. Zero-I/D initialization remains
  unfinished. The HA container lifecycle suite also passed after this change.

## Session timing and replay boundary validation

- Demand-zero measurement debounce now follows the reference's two-PWM-period
  rule. Returning demand resets it. This does not delay furnace commands or
  change the minimum on/off duration; only measurement finalization waits.
- Each session stores its actual PWM period. Live state exposes
  `learning_pwm_seconds` for Recorder. Automatic gain updates require known
  timing matching the current period; older untagged history is not assumed
  to have the current timing. Changing timing invalidates an in-progress sample.
- Replay can replace an untimed session with a matching timed session, never
  the reverse. The previous private sample has no recorded PWM period and
  therefore does not yet qualify for direct automatic gain updates.
- Replay retains the latest pre-window context for unchanged entities without
  fabricating a heat-on transition. Original sensor timestamps remain intact,
  so stale boundary temperatures still fail freshness checks.
- Import confidence stays explicitly limited: raw cycle count alone does not
  demonstrate comparable timing/gains, undisturbed operation, or calibration.
- Tests cover boundary context, stale temperatures, initial-on exclusion,
  brief demand-zero gaps, zero/changed/invalid PWM, replay timing provenance,
  merge preference, and exclusion of unknown/different timing from gain updates.
- The isolated HA runtime suite passed with the demand-zero/PWM changes.
  Additional replay-only boundary tests pass locally. No live changes occurred.

## History-based initialization: measured response evidence

- Completed observations now retain separate physical on intervals and measure
  net temperature change only over those intervals. PWM gaps and post-off coast
  are excluded from the heater-on rate. Interval durations weight the rate.
- Post-off rise, first observed peak delay, and observation duration are recorded
  separately. A peak plateau does not artificially extend the measured delay.
- These are net measured responses, not delivered heating power, building heat
  loss, or an identified plant model. No PID gains are derived or applied yet.
- Historical replay and live diagnostics expose response coverage and nonpositive
  on-rise counts. Old records do not acquire invented response measurements;
  replay must obtain those measurements from actual temperature/output history.
- The existing private history snapshot yielded 10 responses: six had nonpositive
  temperature change during on intervals. Median on-interval rate was zero;
  median post-off rise was 0.31 C, with a 529-second median observed peak delay.
  This is a previously saved, partial history export, not a new live reading or
  complete 14-day collection. It demonstrates delayed measured response, not
  whether the delay comes from the furnace, sensor aggregation, or other effects.
- Tests cover PWM gaps, duration weighting, delayed response, negative on-rise,
  missing endpoints, malformed intervals, peak plateaus, persistence, and invalid
  saved measurements. Plant identification and held-out validation remain required
  before zero-I/D initialization can be enabled. Live HA is unchanged.

## Offline delayed-response model experiment

`tests/thermal_model_lab.py` models a delayed, first-order filtered relay input
plus constant background temperature drift. This is an experimental local
model, not copied Adaptive Climate physics, and not imported by Home Assistant.
Its response time constant is not a building heat-loss time constant.

- Explicit delay/time-constant grids are scored on training windows using
  NumPy least squares. Rank-deficient/ill-conditioned designs are withheld.
- Separate later windows are predicted without refitting, with time-weighted
  error comparisons against persistence and training-fitted drift-only baselines.
- Input contracts reject unknown initial residual heat, overlapping windows,
  sensor gaps, malformed timestamps/intervals, and absent post-off observations.
- Tests generate temperature responses by independent one-second numerical
  integration, rather than using the fitting equation to generate its targets.
- Tests recover the synthetic delay, distinguish delayed heat from no heating,
  expose changed held-out behavior, and prohibit holdout leakage into fitted
  parameters. Boundary grid results are flagged, not approved as calibration.
- Known-rest initial conditions are not yet established from the saved actual
  history, so the synthetic result is not claimed as a fit of the user's house.
- No gain proposal or automatic acceptance exists. Real-history model adequacy,
  uncertainty/excitation criteria, and compatible PID synthesis remain unfinished.
  The runtime integration and its dependency manifest are unchanged this pass.

Least-squares API reference: https://numpy.org/doc/stable/reference/generated/numpy.linalg.lstsq.html

## Saved-history model trial (offline)

- Added `tests/thermal_history_lab.py` to extract isolated physical heating pulses
  with unchanged controller settings, uninterrupted temperature samples and
  post-off coverage. Relay and sensor timestamps stay separate; no synthetic
  temperature samples are interpolated at relay edges.
- Prior recorded off time bounds residual filter response for each candidate
  model. The experimental tolerance is 0.001 of normalized filter state, not
  a measured zero-heat assertion or a physical temperature guarantee.
- Holdout initial conditions cannot select a different training fit: insufficient
  holdout quiet time returns an explicit uncertainty status.
- The saved private export produced five windows. Three earlier windows trained
  the model and two later windows were held out. Candidate delay grid:
  0/60/120/240/480 seconds; response-time grid: 60/120/300/600 seconds.
- This trial selected delay 240 seconds and response time 120 seconds, with
  held-out RMSE 0.0618 C versus persistence 0.2230 C and drift-only 0.0810 C.
  Those are limited predictive results from the saved export, not verified
  physical furnace parameters or ready-to-apply PID gains.
- The export lacks complete command-context IDs. These trials cannot establish
  absence of manual/external intervention, robust model uncertainty, or behavior
  across the full two-week period. No thresholds were relaxed to obtain a fit.
- Per-window RMSE, maximum error and baseline comparisons are exposed separately
  so a favorable aggregate cannot hide a bad individual validation window.
- 194 standalone tests pass. Runtime integration files, dependencies, live HA,
  thermostat gains and all user settings are unchanged by this offline work.

## Expanded MCP history and day-wise validation

- Read-only MCP history query covered August 27 to September 10, 2026, ending
  22:39:58 UTC. The complete returned furnace-switch series has 480 rows, with
  ON activity on September 5-10. Temperature and thermostat history were then
  paginated for those days: 17,075 temperature and 2,713 deduplicated thermostat
  rows. Snapshot is private/ignored at `tests/local_history_mcp_20260910.json`.
- Extracted 33 isolated windows, excluding controller changes, short pulses,
  and reheating during the coast period. The MCP history response still lacks
  command context IDs; no manual-intervention absence is asserted.
- An exploratory 22/11 chronological split gave 0.0652 C held-out RMSE versus
  0.2374 C persistence and 0.0977 C drift-only, but its response-time choice was
  at the tested grid boundary. This prompted a wider grid, not automatic approval.
- Added expanding whole-day validation using America/St_Johns. Windows belong
  to their completion day, preventing midnight-crossing training leakage.
  Each fold fits only earlier days; the later day's temperatures cannot choose
  parameters. This is exploratory model development, not a pristine final test
  set, since the data have already informed model/grid review.
- With delay grid 0/60/120/180/240/300/480 and response-time grid
  0/30/60/120/300/600 seconds, the September 9 and 10 folds both selected
  300-second delay and 30-second response time. Their held-out RMSEs were
  0.0446 C and 0.0837 C, respectively. Every window beat its drift-only baseline.
  September 6 was withheld for uncertain initial residual response, not refitted
  using its validation temperatures. Some days lacked enough isolated windows.
- These delayed-response parameters describe the aggregate measured signal,
  not independently identified physical furnace latency or building heat loss.
  The model remains offline, with no gain synthesis or automatic acceptance.
- 199 standalone tests pass, including chronology, future-data isolation,
  midnight boundaries, missing provenance, and insufficient-day handling.
  All live access was read-only; no reload, restart, control or config edits.

## Initial PID gain calculation implemented

- `initial_pid.py` now calculates actual initial gain candidates for the fitted
  integrating-plus-lag model. It uses SIMC Table 5.1 and equation 5.30 from
  Skogestad/Grimholt, not Adaptive Climate's building-property lookup gains.
- Model rate is converted from C/hour at full input to C/second per host output
  unit. Series PID is converted to parallel Kp/Ki/Kd; the existing controller's
  derivative remains on measurement. Feedback coefficients are equivalent;
  identical setpoint-step behavior is not asserted.
- The latest recent-history fold, with an explicit provisional 900-second
  closed-loop design time and 100-unit output span, produces Kp 46.7731532,
  Ki 0.00968388265 and Kd 1394.479102. These are not deployed or approved gains.
  The 900-second design input is not a changed minimum-cycle or PWM setting.
- The design-time choice is provisional, not yet automatically selected. The
  identified 30-second lag is smaller than its 300-second delay; the calculator
  flags that derivative is not lag-dominant, consistent with the source's caution
  on when derivative is most useful. No claim that this PID beats PI is made.
- Tests verify numerical formulas, series/parallel feedback equivalence, output
  scaling, real installed PID integral/derivative terms in seconds, invalid and
  extreme inputs, slower design gains, and zero-lag behavior (Kd stays zero).
- The calculator is pure and not wired into the gain-application path. Model
  acceptance, PWM/minimum-cycle closed-loop testing, noise robustness and
  automatic design-time selection remain necessary. Existing zero-gain guard
  remains in place; current thermostat settings are untouched.

Source: https://skoge.folk.ntnu.no/publications/2012/skogestad-improved-simc-pid/PIDbook-chapter5.pdf

## Closed-loop testing rejected the first initial-gain candidates

- `tests/closed_loop_lab.py` executes the actual source methods for PID calculation,
  control output rounding/clamping, PWM, and heater on/off minimum-cycle checks.
  HA states/services and the clock are simulated; lifecycle decorators are omitted
  in this harness and remain covered by the separate lifecycle tests.
- The thermal plant is synthetic, with one-second integration, 30-second sensor
  samples, and instantaneous output feedback. Six-hour constant-drift tests are
  extrapolations of the short-window model, not verified six-hour house behavior.
- Tests preserve 900-second PWM and 150-second minimum on/off times. Explicit
  thermostat OFF is also tested and can stop heat without waiting for minimum on.
- Compared baseline Kp100/Ki0/Kd0 against the calculated candidates in nominal,
  cold-start, longer-delay, stronger-heating, sensor-noise, and higher-loss cases.
  The 900-second-design candidate worsened nominal overshoot from 0.219 C to
  0.259 C, last-third MAE from 0.101 C to 0.123 C, and heat starts from four to
  five. Cold-start overshoot worsened from 0.219 C to 0.379 C. It is rejected.
- Design times 450, 1800 and 3600 seconds were also tested. Smoother settings
  helped some overshoot cases but regressed temperature error or cycling in
  others. All four candidates fail the local zero-regression screening gate.
- `tests/gain_validation_lab.py` reports missing/nonfinite metrics and individual
  scenario regressions rather than hiding them in aggregate scores. Passing
  would only allow further validation, never live application. Its strict policy
  is local and experimental, not a claim that SIMC guarantees these properties.
- 217 standalone tests pass. No candidate gains were applied; runtime control
  files, live thermostat settings and Home Assistant remain unchanged this pass.

## Earlier prototype notes (superseded)

Not installed. Live Home Assistant has not been modified or restarted.

## Implemented locally

- Existing installed integration copied to installed-snapshot as the baseline.
- Optional automatic anticipatory cutoff (`adaptive_learning: true`), with
  `adaptive_observe` retained for development-only observation. Default false
  preserves thermostats that have not opted into the feature.
- Persisted completed thermal cycles and bounded residual-heat hold.
- Recorder importer requests the recent 14 days in daily batches.
- Background import after startup; task and pending startup listener canceled on removal.
- Replay excludes initial-on partial cycles, missing/unavailable data, setpoint
  changes, recorded direct manual output changes, and reheating during settling.
- Imported records merge with live records; records older than the requested
  window are excluded at import. No incomplete historical cycle is restored.
- Runtime-dependent rise prediction from at least six recent cycles and at
  least three comparable runtimes; median-spread rejection and lower-quartile
  estimate, with a 1 C maximum anticipatory offset. No distant extrapolation.
- Existing PID remains responsible for heat demand. Adaptation can only inhibit
  it, honors minimum on time, never changes target/presets/gains, and freezes
  integral accumulation while inhibited. This is thermal anticipation, not
  continuous identification of PID coefficients.
- Waits after an anticipatory cutoff until measured cooling resumes or 15 minutes
  elapse, whichever comes first. A target/mode change cancels the hold. This
  timeout is an internal bound, not a change to configured minimum cycle time.
- Actual output edges capture runtime and existing minimum-cycle clock.
- Duplicate historical/live pulses are merged rather than counted twice.
- Unload cancels import and flushes saved learning without commanding heat.
- 41 standalone tests, including actual control/unload methods exercised with
  fake HA dependencies, empty Recorder data, startup holds and cancellation.
- Recorder API signature checked against installed HA using read-only SSH.

## Preliminary real-data replay

September 9 00:00 through September 10 15:15, local time, 2026:
4,693 temperature samples plus climate attributes and furnace relay events.
Nine candidate cycles. Short burns showed about 0.25-0.40 C post-off rise;
an approximately eight-minute burn showed 0.93 C rise. These are associations,
not isolated furnace response measurements. MCP export lacks user context;
manual/external influences cannot all be ruled out in this sample.

The local JSON export is ignored by git and is not for publication.

## Still required before deployment

- Validate manual context propagated through the actual furnace bridge. Direct
  user context is excluded, but that cannot identify every external intervention.
- Validate actual full-window Recorder import in HA, not just API/mock tests.
- Validate reload lifecycle and persistence in HA.
- Replay prospective control against held-out cycles; do not claim a
  counterfactual room temperature is proven by observational history.
- Enable the opt-in feature on the existing heating entity only after validation
  and deployment approval; preserve thermostat identity, presets and schedules.
- Review baseline changes separately from new code; do not publish the entire
  upstream diff as newly authored work.

The local code now can automatically shorten burns, but it has not been deployed
or tested controlling the real furnace. Existing PID gains are not rewritten.
