# Local package validation

## Package validation on 3 November 2026

The packaged local route passed 18 automated authority checks and 8 desktop and tooling checks. The existing controller and C2 regression subset passed 91 tests. Authority coverage includes competing claim and enable rejection, a real competing CLI refusal, correlated PWM, hold release, STOP and disabled reset, publication timeout, missing encoder warnings, delayed and silent feedback, dropped commands, explicit recovery, settings validation, and cleanup that leaves an unrelated session running.

The changed ceiling check confirmed left 14 and right −2 at ceiling 31 for throttle 0.20 and steering −0.25. A response sensitivity check at the same ceiling observed 64 synthetic pulses with a 0.05 second response and 14 with a 1.5 second response over approximately 0.605 seconds. This is model evidence, not calibrated physical speed. Desktop checks verified GUI and graph startup/liveness, CLI and YAML precedence, fault clearing while disabled, startup with configured severe faults, and removal of the owned GUI, graph, OC and simulator processes. Manual button interaction in the visible GUI was not repeated by this tooling smoke check; hold/release and STOP/reset behavior were exercised by the ROS C2 test client and existing GUI/controller regressions.

Current evidence: `evidence/tests/2026-11-03-authority-complete/result.json`, `evidence/tests/tooling-7ede3f2a/result.json`, and `evidence/controller-tests-2026-11-03.log`. These runs used domain 97 and unique namespaces for package sessions. They establish local simulation behavior only. No two-machine network run, firmware flashing, electrical measurement or powered motor test was performed for this package.

