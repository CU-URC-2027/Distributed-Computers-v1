# Open-loop v2 validation — updated 29 October 2026

## Completed

| Layer | Result | Evidence |
|---|---|---|
| ROS 2 Jazzy workspace | All 6 packages build with symlink installation | `validation/v2-build.log` |
| Python regressions | **183 passed**, including real ROS/PTY and hidden Tk GUI | `validation/v2-tests.log` |
| End-to-end route | 11 tests pass: PWM/identity, hold release, reclaims, STOP/reset, missing encoder warnings, stale feedback, MCU restart, OC SIGKILL, delayed tokens, OC restart, GUI focus behavior, and hardware launch configuration across the cases | `src/mini_rover_oc/test/test_end_to_end.py` |
| Firmware parity | **131 native/Python checks pass**, using shared C++ board code | `validation/v2-tests.log`; firmware `tests/test_conformance.py` |
| Historical guard | Parser, ordering, watchdog, STOP, ownership, rollover pass | `validation/v2-tests.log` |
| UNO R4 WiFi firmware | Compile succeeds, core 1.6.0, native USB CDC; **61,068 bytes flash (23%), 11,744 bytes static RAM (35%)** | `validation/firmware-build-evidence.json`; firmware `.build/firmware/compile.log` |
| Source/build provenance | Recorded source hashes match current files; binary/ELF/HEX hashes verified | `SOURCE_MANIFEST_V2.json`; firmware build evidence |

Tests exercise independent 250 ms drive/input expiry, 350 ms feedback expiry,
200 ms challenge expiry, exact integer IDs, malformed framing, replay/ownership,
STOP preemption, unavailable encoder warnings, command-application correlation,
first-fault retention, and explicit recovery. GUI tests use hidden windows and
only the simulated PTY. The separate initial board capture below opened the verified
v2 physical port for read-only telemetry after upload.

The integration run found and fixed a reclaim race: an old accepted command ID
could previously satisfy C2's readiness check. Each claim now waits for a newly
acknowledged neutral command. Other fixes include recoverable-error reclaim,
acknowledgment rollback rejection, disabled-confirmation ordering, retired-boot
rejection, service request replay protection, asynchronous error display, and
uint64 exhaustion parity.

The firmware must be compiled using its build script. The pinned UNO R4 WiFi
core defaults to a bridge UART; this build enables native USB CDC and omits the
core's incompatible BACKTRACE_SUPPORT path. Runtime USB enumeration can change
following upload. No core installation files were modified.

## Board upload and initial telemetry completed

After the operator confirmed motor supply disconnected and wiring matching the
pin map, Arduino CLI uploaded the compiled v2 image successfully. Runtime USB
identity changed as expected to:
`/dev/serial/by-id/usb-Arduino_UNO_R4_WiFi_340328293931363167EA33324B572D71-if00`.
Upload log: `validation/firmware-upload.log`.

A read-only USB capture recorded **101 valid v2 feedback frames in 9.98 seconds**,
with a maximum observed host receipt gap of **102.2 ms**. Every frame reported
disabled, zero side PWM, no bound serial owner, and all encoder channels explicitly
unavailable. This validates initial runtime protocol/telemetry and software-reported
disabled state. It does not measure STBY/PWM electrically.
Evidence: `validation/board-disabled-capture.jsonl` and `board-capture-summary.json`.

After reconnect, the board enumerated as CMSIS-DAP and produced no data at 115200.
A single physical reset did not restore native USB. A CLI readback override was
ignored by its recipe and caused a second upload of the identical verified image;
this is recorded in `validation/reupload-after-reconnect.log`. Native USB returned.
This is an unresolved startup/reconnect issue, not proof of a source regression.
No bootloader or ESP firmware was changed.

With motor supply still disconnected, the **actual C2→ROS OC→USB MCU→C2 route
passed**: 235 OC states and 127 hardware MCU samples, 10 seconds of fresh disabled
monitoring, neutral enable, forward/reverse/left/right commands at ±16 PWM with
MCU-applied identities, release to zero, STOP, reset remaining disabled, and C2
silence disabling/revoking permission. Final state was confirmed disabled.
Evidence: `validation/board-route-20261029/result.json`, `c2-state.jsonl`,
`c2-samples.jsonl`, and corresponding OC whole-run logs. A compact result is in
`validation/board-route-summary.json`.
These are software-state confirmations with motor supply disconnected; electrical
signals and wheel movement have not been measured.

## Subsequent boot and live hardware dashboard

A subsequent observed boot produced a new MCU identity
`e91469ae3a53c6dda5d2173a461337e0` on native USB, with 31 valid frames reporting
zero PWM and disabled state. The exact reset/power-cycle method was not confirmed;
this recovery does not close the earlier intermittent reconnect issue.
Evidence: `validation/startup-followup.json`.

The hardware OC and C2 GUI are now running under `/rover1`, domain 92, using the
actual native USB device. A separate ROS observer confirmed 44 state messages,
22 hardware chassis samples, fresh/known MCU state and disabled outputs. No simulator
was launched. Evidence: `validation/hardware-dashboard-verification.json`,
`hardware-dashboard.log`, and `hardware-dashboard-session.json`.
Motor supply remains disconnected per the operator's confirmation. The GUI starts
without input ownership or enable authorization. The live OC whole-run log is under
`run_logs/`.

## Physical work still pending

- Operator confirmed wiring/power isolation and no A2 conflict. Electrical polarity,
  actual output waveforms, and hand-turned encoder evidence still need bench measurements.
- Reliable startup after USB/power cycles, STBY/PWM checks, hardware watchdog timing, hand-turned
  encoder evidence, and raised-wheel motor movement/feedback remain **PENDING**.
- Software tests do not establish mechanical stopping time, physical motion,
  encoder calibration, signed speed, or full hardware acceptance.

Follow ARDUINO_MIGRATION_TEST_PLAN.md for the physical gates. Retain the 63/255
ceiling and never automatically raise it to overcome a stationary motor.

## Reproduce

```bash
cd /home/liamolczak/weeny_ws
./scripts/build.sh
./scripts/run_tests.sh
/home/liamolczak/mini-rover-prototype/scripts/build_firmware.sh
```

Tests default to localhost domain 97; operation uses domain 92. Start the simulator
with `./scripts/run_demo.sh`. Launch actual hardware only after physical prerequisites
and verified v2 upload, using the hardware launch described in README.md.

Baseline archives: `/home/liamolczak/rover-backups/20261028T164340Z`.
