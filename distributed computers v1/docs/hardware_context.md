# Validation and hardware context

This reference separates recorded software results, unpowered physical-board communication and physical motor evidence. The package's runnable workflow uses a simulated Arduino over a local PTY. It never flashes firmware or runs powered hardware tests. Current package-specific test results are saved separately under `evidence/`; the dated historical results below are not a claim that those tests were rerun today.

## Package validation on 3 November 2026

The packaged local route passed 18 automated authority checks and 8 desktop and tooling checks. The existing controller and C2 regression subset passed 91 tests. Authority coverage includes competing claim and enable rejection, a real competing CLI refusal, correlated PWM, hold release, STOP and disabled reset, publication timeout, missing encoder warnings, delayed and silent feedback, dropped commands, explicit recovery, settings validation, and cleanup that leaves an unrelated session running.

The changed ceiling check confirmed left 14 and right −2 at ceiling 31 for throttle 0.20 and steering −0.25. A response sensitivity check at the same ceiling observed 64 synthetic pulses with a 0.05 second response and 14 with a 1.5 second response over approximately 0.605 seconds. This is model evidence, not calibrated physical speed. Desktop checks verified GUI and graph startup/liveness, CLI and YAML precedence, fault clearing while disabled, startup with configured severe faults, and removal of the owned GUI, graph, OC and simulator processes. Manual button interaction in the visible GUI was not repeated by this tooling smoke check; hold/release and STOP/reset behavior were exercised by the ROS C2 test client and existing GUI/controller regressions.

Current evidence: `evidence/tests/2026-11-03-authority-complete/result.json`, `evidence/tests/tooling-7ede3f2a/result.json`, and `evidence/controller-tests-2026-11-03.log`. These runs used domain 97 and unique namespaces for package sessions. They establish local simulation behavior only. No two-machine network run, firmware flashing, electrical measurement or powered motor test was performed for this package.

## Recorded software baseline

The workspace's `VALIDATION_V2.md`, updated 29 October 2026, records a successful six-package ROS 2 Jazzy build and 183 passing Python regressions. These include ROS/PTY integration and hidden Tk GUI checks. It also records 131 native/Python firmware parity checks and a successful UNO R4 WiFi build using renesas_uno core 1.6.0. The recorded binary used 61,068 bytes flash and 11,744 bytes static RAM.

The tests cover normalized PWM and correlation identities, exclusive ownership, neutral enable, hold release, STOP/reset, fresh reclaims, replay and malformed framing, timer rollover, missing encoder warnings, stale feedback, process restarts and explicit recovery. Passing simulator or native tests does not establish electrical behavior, encoder calibration, mechanical stopping time or physical wheel movement.

Historical evidence is retained in `~/weeny_ws/validation/`, including `v2-build.log`, `v2-tests.log` and `firmware-build-evidence.json`. Firmware build output and compiler logs are in `/home/liamolczak/mini-rover-prototype/.build/firmware/`. The inventory in this package records source and evidence identities present at packaging time.

## Unpowered board communication on 29 October 2026

Before upload and board communication, the operator confirmed the motor supply was disconnected, wiring matched the stated pin map and there was no A2 encoder/sonar conflict. This is an operator confirmation, distinct from electrical instrumentation or a newly verified physical inspection.

The recorded v2 upload succeeded. A read-only capture then received 101 valid feedback frames over 9.98 seconds, with a maximum host receipt gap of 102.2 ms. Frames reported disabled outputs, zero side PWM, no bound owner and explicitly unavailable encoders. These establish protocol telemetry and software-reported state for that run. They do not measure STBY or PWM voltage.

A later recorded unpowered C2-to-OC-to-USB-MCU route check received 235 OC states and 127 hardware MCU samples. It exercised disabled monitoring, neutral enable, forward/reverse/turn commands at ±16 PWM, correlated MCU-applied identities, hold release to zero, STOP, reset remaining disabled and C2 silence withdrawing permission. Its final state was confirmed disabled. Motor power remained disconnected, so the run could not demonstrate powered motor motion.

Evidence paths include `validation/board-capture-summary.json`, `board-disabled-capture.jsonl`, `board-route-summary.json` and `board-route-20261029/result.json` in the active workspace. OC logs retain whole-run state and serial correlation.

## Startup and reconnect issue remains open

After a USB reconnect, the board presented a CMSIS-DAP/bridge identity rather than expected native USB CDC and produced no data at 115200. A single physical reset did not restore native USB. The historical log records a second upload of the identical verified image after an attempted readback override was ignored by the recipe; native USB returned. No bootloader or ESP firmware was changed.

A subsequent observed boot produced a new MCU boot identity and 31 valid disabled, zero-PWM frames. The exact reset or power-cycle method was not confirmed, so that recovery does not close the intermittent startup/reconnect issue. Retained evidence includes `reupload-after-reconnect.log` and `startup-followup.json`.

The historical validation document also describes a hardware dashboard that was running then. That paragraph is a dated session observation, not the present operating mode or proof that the board is currently connected. The requested current workflow is local simulation.

## Firmware reference and physical limitations

The actual board implementation is `/home/liamolczak/mini-rover-prototype/mini_rover_mule/mini_rover_mule.ino`, with the adjacent `control_core.h` and `pin_map.h`. Use that adjacent pin map as the build input; the repository's root-level pin header is historical. The board codec/state machine is dependency-free C++ and is shared by native checks and the board build.

| Function | Current source pin map | Evidence still required |
|---|---|---|
| Left paired motor outputs | DIR D2/D4, PWM D3 | Pair wiring, polarity and waveform |
| Right paired motor outputs | DIR D6/D7, PWM D5 | Pair wiring, polarity and waveform |
| Shared standby | D8 | Low at startup and measured shutdown behavior |
| Front-left encoder | A2 | Hand-turn channel mapping and pulses |
| Front-right encoder | D11 | Hand-turn channel mapping and pulses |
| Rear-left and rear-right | D9 and D13 | Unsupported interrupt mappings; remain unavailable |

Core 1.6.0 lacks the required interrupt mappings for D9/D13. Rear encoder measurements remain invalid. A2/D11 become valid only after an interrupt pulse is observed during the current boot. That is not calibrated sensing or a guaranteed unplug-detection mechanism. Direction validity remains false for every channel. Unchanged counts cannot distinguish a stationary wheel from a disconnected single-channel sensor.

The source initializes standby low and zero PWM before accepting authority, enforces signed PWM within ±63 and checks the valid-drive watchdog. USB is the sole motor authority in this build. Peripheral execution, keyboard/UDP control and Wi-Fi motor authority are excluded. These source properties require physical measurement before claiming electrical timing or motor behavior. Shutdown is disabled/coast; it does not prove immediate mechanical stopping.

## Pending hardware acceptance

Reliable USB startup through reset/reconnect cycles remains pending. Electrical output polarity, standby pull-down, PWM waveforms and watchdog timing still need instrumentation. Hand-turned encoder mapping and raised-wheel motor movement with correlated feedback also remain pending. No calibrated speed, signed encoder speed, closed-loop control, stopping distance or powered-motor result is claimed here.

Use the separate Arduino migration test plan for those gates. Begin with motor power disconnected, verify the physical arrangement again, retain a reachable cutoff for a later powered test, and keep commands within ±63. If a motor does not move, do not automatically increase the ceiling. Record electrical shutdown timing separately from mechanical stopping behavior.

## Historical material and provenance

Earlier v1 velocity/wheel-speed interfaces and MOTOR1/keyboard/UDP instructions are historical. They are incompatible with the current normalized OPEN_LOOP_PWM v2 route. The old character parser can interpret letters within JSON as motor commands; it is not a compatible target for current JSON writers. The original working files were preserved in `/home/liamolczak/rover-backups/20261028T164340Z`.

The firmware README's statement that the change had not been flashed describes an earlier implementation snapshot. The newer 29 October workspace validation and upload logs supersede that statement for the recorded board run. Neither historical document authorizes a new hardware action in this package.

The source inventory records absolute source paths, SHA256 hashes, byte sizes and UTC modification times, with packaging capture time on 3 November 2026. Modification time is a filesystem property, not proof of test execution time. The inventory identifies the current files; dated validation logs identify the recorded runs. This documentation-and-tools package leaves active source code and firmware unchanged.
