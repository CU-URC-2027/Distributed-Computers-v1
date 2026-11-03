> Historical pre-v2 document. For the active OPEN_LOOP_PWM contract and tests, use [INTERFACE_V2.md](INTERFACE_V2.md), [README.md](README.md), and [VALIDATION_V2.md](VALIDATION_V2.md). Older speed/encoder-enable assumptions do not apply to this milestone.

# Validation — 2026-10-25

## Passed

- ROS 2 Jazzy: `colcon build --symlink-install` built both packages.
- `colcon test` and `colcon test-result --verbose`: **23 tests, 0 errors,
  0 failures, 0 skipped**. Tests include real pySerial/Linux PTY I/O with ROS
  nodes, malformed/partial input, stale status, disconnect/reconnect, and
  verifying that no serial commands are sent.
- Installed launch file exposes required `port` and default `baud=115200`.
- Separate-process launch smoke test: a Linux PTY fed records to the installed
  `motor_bridge` executable launched with `ros2 launch`; another ROS node
  received the expected wheel counts. Ctrl+C shut down the launch and bridge
  cleanly. Log: `artifacts/launch-smoke.log`.
- Arduino compile: `arduino:renesas_uno:unor4wifi`, Renesas UNO core **1.6.0**.
  Program storage **92,268 / 262,144 bytes (35%)**; static RAM **12,272 / 32,768
  bytes (37%)**. Compile outputs are in ignored `artifacts/firmware/`.
- Source credentials are confined to ignored `arduino_secrets.h`; the pin map
  and Xbox script were imported from upstream commit
  `93656908dac2f201bcc514b493b14a19beba6ce2`.

Arduino dependencies used: SH110X 2.1.15, GFX 1.13.0, BusIO 1.17.4,
NeoPixel 1.15.5, MPU6050 2.2.9, Unified Sensor 1.1.15. The isolated toolchain
and libraries were installed under `/tmp/mini-rover-tools`, not system-wide.

Test execution set `ROS_LOG_DIR=/tmp/mini-rover-ros-logs` because the sandbox
cannot write the normal home-directory ROS logs. Separate-process discovery
required localhost networking outside the sandbox; that check passed.

## Not yet verified on hardware

No Arduino serial device was present. No firmware was flashed, no motor was
commanded, and no physical sensor readings were collected.

The remaining acceptance checks in README.md cover the actual board/pin map,
four working encoder interrupts, native USB enumeration and upload recovery,
approximately 10 Hz telemetry, Wi-Fi Xbox driving, Wi-Fi/OLED independence,
USB backpressure, and hardware unplug/restart recovery. Firmware compilation
and PTY tests do not establish these physical behaviors.
