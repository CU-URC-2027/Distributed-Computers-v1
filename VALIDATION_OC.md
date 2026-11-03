> Historical pre-v2 document. For the active OPEN_LOOP_PWM contract and tests, use [INTERFACE_V2.md](INTERFACE_V2.md), [README.md](README.md), and [VALIDATION_V2.md](VALIDATION_V2.md). Older speed/encoder-enable assumptions do not apply to this milestone.

# OC prototype validation — 2026-10-26

Completed on this Ubuntu computer with ROS 2 Jazzy. The deployable source snapshot and built workspace are at `/home/liamolczak/weeny_ws`.

## Update — 2026-10-27: persistent manual control

- Full regression runner: **127 Python tests passed** plus the C++ guard checks.
- ROS 2 / Manual numbers retains enable at zero and applied nonzero commands when focus leaves the GUI. The GUI event loop continues publishing; there is no maximum-run or idle timer while communication remains healthy.
- A 30-second integration test used the actual Tk loop, ROS messages and separate OC/Arduino simulator processes with focus outside the application throughout. Command IDs advanced, the same session stayed enabled, and wheel feedback remained fresh.
- Pause publication still disables, resuming publication does not automatically enable, deliberate re-enable works, and closing the GUI ends the command.
- Existing STOP/reset, feedback-loss, restart, fault, Xbox focus-loss and legacy GUI regressions passed. The 250 ms watchdogs, ROS messages and serial protocol are unchanged.
- Updated instructions and source are synchronized with `~/weeny_ws`. Evidence: `validation/persistent-manual-tests.log`.

## Initial verification — 2026-10-26

- All **six ROS packages build** in both the project workspace and `~/weeny_ws`.
- `~/weeny_ws/scripts/run_tests.sh`: **124 tests passed** plus the standalone C++ C2Guard checks.
- Coverage includes 39 core/protocol/serial/logging tests, 9 ROS/PTY/GUI/CLI integration tests, 34 original C2 Python tests, 19 original Tk regressions, and 23 original read-only USB telemetry tests.
- Numeric commands travel through actual ROS DDS into the OC process, across a PTY into a separate Arduino simulator, and back as named wheel feedback with timestamps, validity and applied-command identity.
- Verified manual GUI Apply behavior, decimal input validation, editing without applying, STOP/reset, source/transport disarming, wheel-speed error, independent watchdogs, feedback silence, delayed samples, encoder faults, process death, and simulator restart/reconnection.
- An abrupt OC process kill leaves the independent Arduino model disabled after its watchdog expires.
- STOP reset waits for an acknowledged neutral/non-STOP intent, avoiding cross-topic/service delivery-order races.
- Installed launch smoke check: the headless client commanded `0.20 m/s`, `-0.25 rad/s` for two seconds, then disarmed and exited successfully.
- Installed GUI launch smoke check: simulator, OC and GUI all started, then all three exited cleanly on Ctrl+C and removed the PTY symlink. Launch uses direct installed executables so shutdown does not orphan a `ros2 run` child.
- Source snapshot checksums match the project sources. `SOURCE_MANIFEST.json` lists copied source/document files; build products, caches and local credential headers are excluded.

Evidence in the deployed workspace: `validation/tests.log`, `validation/gui-launch.log`, and `validation/build.log`. Whole-run simulation data is in `run_logs/`.

The ROS library version had changed since the earlier cached build. Reconfiguring with `./scripts/build.sh --cmake-clean-cache` refreshed the cached paths; the clean `~/weeny_ws` build also passed.

## Boundaries

This validates a software prototype, not physical motor control. No Arduino was flashed and no physical motors were commanded. The existing read-only `MOTOR1` bridge/firmware remain separate from the new control protocol.

Before physical integration, resolve rear encoder interrupt support on D9/D13, validity reporting in the older raw telemetry format, direction sensing and encoder calibration, actual geometry, paired driver channels, and hardware disable behavior. See `OC_PROTOTYPE.md` for interfaces and next-stage boundaries.
