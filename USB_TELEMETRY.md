> Historical pre-v2 document. For the active OPEN_LOOP_PWM contract and tests, use [INTERFACE_V2.md](INTERFACE_V2.md), [README.md](README.md), and [VALIDATION_V2.md](VALIDATION_V2.md). Older speed/encoder-enable assumptions do not apply to this milestone.

> This is the earlier **read-only physical Arduino telemetry** milestone. For the current C2/OC/simulated-Arduino prototype, start with [README.md](README.md).
> Paths containing `ros2_ws/` refer to the original project layout; in `~/weeny_ws`, packages are directly under `src/`.
> Rear encoder interrupt support and measurement validity remain unresolved; see [OC_PROTOTYPE.md](OC_PROTOTYPE.md#verification-and-physical-boundaries) before physical testing.

# Mini rover: USB motor telemetry for ROS 2

The UNO R4 WiFi sends encoder feedback over USB-C while the existing Xbox
controller continues to drive over Wi-Fi. The Linux bridge only reads serial
data; it never sends drive, LED, or polling commands.

Target host: Ubuntu 24.04, ROS 2 Jazzy, Python 3.12. The firmware and unchanged
Xbox script were imported from [mafl8398/mini-rover-prototype](https://github.com/mafl8398/mini-rover-prototype/tree/93656908dac2f201bcc514b493b14a19beba6ce2)
at commit `93656908dac2f201bcc514b493b14a19beba6ce2`. Existing research in `tmp/`
is unrelated to the build.

## What you get

- `firmware/mini_rover_mule/`: complete Arduino sketch, pin map, and USB console.
- `teleop_controller.py`: original Xbox-over-UDP controller.
- `ros2_ws/src/mini_rover_interfaces/`: named `MotorTelemetry` ROS message.
- `ros2_ws/src/mini_rover_bridge/`: Python serial reader, launch file, and tests.

The four encoder counters increase for rising edges in **either direction**.
They are raw pulse counts, not RPM, signed wheel position, or odometry. Throttle
and steering are the last received commands; `driver_enabled` is the standby
pin level, not proof of motion. Battery, IMU, sonar, and ROS motor control are
outside this milestone.

## Firmware setup

Before uploading, confirm the rover is an UNO R4 WiFi and that its existing v6
firmware uses the supplied `pin_map.h`. This implementation has not been flashed
or tested on physical hardware. In particular, verify all four interrupt inputs
actually count on the wired board.

Open `firmware/mini_rover_mule/mini_rover_mule.ino` in Arduino IDE, select **Arduino
UNO R4 WiFi**, and install the Arduino Renesas UNO core plus these libraries:

- Adafruit SH110X, Adafruit GFX Library, and Adafruit BusIO
- Adafruit NeoPixel
- Adafruit MPU6050 and Adafruit Unified Sensor

The repository's older README named SSD1306; this v6 sketch uses **SH1107**.

Wi-Fi settings live in the ignored `arduino_secrets.h` beside the sketch. This
local workspace already has a copy populated from the supplied firmware. For a
fresh checkout, copy `arduino_secrets.h.example` to `arduino_secrets.h` and enter
your network details. Do not commit the populated header or share compiled
firmware without considering that it contains these credentials.

With Arduino CLI installed, from this project's root:

```bash
arduino-cli core install arduino:renesas_uno@1.6.0
arduino-cli lib install 'Adafruit SH110X' 'Adafruit NeoPixel' 'Adafruit MPU6050'
arduino-cli compile --fqbn arduino:renesas_uno:unor4wifi firmware/mini_rover_mule
```

### USB behavior on the UNO R4 WiFi

The board's default `Serial` uses a blocking UART through its ESP32 bridge and
does not implement a usable `availableForWrite()`. The sketch therefore uses
the Renesas core's **native `SerialUSB`** via `UsbConsole` and switches the
board's onboard USB mux with `__USBStart()`. The same physical USB-C cable is
used. This changes the Linux USB identity/serial-device path after boot; inspect
the running sketch's device, not only the bootloader device. WiFiS3 keeps its
original UART mapping; the build does not undefine `NO_USB`.

Use a data-capable USB cable. Native USB uses CDC line coding; both sides are
configured for 115200, though native USB throughput is not UART baud limited.
Keep the default serial DTR behavior. Do not open the port at 1200 baud except
when deliberately requesting the bootloader. If a future upload cannot locate
the board, double-tap RESET to return to the bootloader and select its port.

Telemetry checks host connection and room for the **entire record** before
writing. It skips samples under backpressure rather than waiting or building a
queue. Startup/debug output also drops writes when full or disconnected, so
early messages may be missed. Every motor record has a leading and trailing
newline to recover framing after interrupted output.

Wi-Fi gets one initial attempt with a 10-second connection timeout (plus modem
command overhead). USB telemetry begins after setup even if Wi-Fi fails. There
is no application-level reconnect loop; restart the Arduino to retry. The OLED
uses the IP cached at setup, avoiding repeated network calls in the display
loop. Existing motor control and LED mode semantics remain unchanged. The UDP
read size also leaves room for its terminating null byte.

## Build ROS 2 packages

```bash
cd '/home/liamolczak/Documents/ChatGPT/Deep dive into motor prototype/ros2_ws'
source /opt/ros/jazzy/setup.bash
colcon build --symlink-install
source install/setup.bash
```

Dependencies are declared in the package manifests. They are already available
on this computer. On another prepared ROS Jazzy host, resolve missing packages
with `rosdep install --from-paths src --ignore-src -r -y` before building.

## Connect and run

1. Connect the Arduino, wait for its sketch to start, and inspect the device:
   ```bash
   ls -l /dev/serial/by-id/
   ```
   If this directory is absent, inspect `ls /dev/ttyACM* /dev/ttyUSB*` and
   `udevadm info --query=property --name=/dev/ttyACM0` for the actual device.
   Do not assume `ttyACM0` remains stable across uploads or reconnects.
2. Close Arduino Serial Monitor and any other serial reader.
3. In a terminal with the workspace sourced, substitute the actual device:
   ```bash
   ros2 launch mini_rover_bridge motor_telemetry.launch.py port:=/dev/serial/by-id/YOUR_DEVICE
   ```
   Equivalent direct invocation:
   ```bash
   ros2 run mini_rover_bridge motor_bridge --ros-args -p port:=/dev/serial/by-id/YOUR_DEVICE -p baud:=115200
   ```
4. In a second sourced terminal:
   ```bash
   ros2 topic echo /rover/motor_telemetry
   ros2 topic hz /rover/motor_telemetry
   ros2 topic echo /diagnostics
   ```
   These are separate foreground commands; stop one with Ctrl+C before running
   the next, or use separate terminals.
5. Record a session from a directory where you want the recording saved:
   ```bash
   ros2 bag record -o motor_feedback /rover/motor_telemetry /diagnostics
   ```

If serial access is denied, check `ls -l /dev/ttyACM0` and `id`; on Ubuntu the
device commonly belongs to `dialout`. Add your user to that group if needed and
log out/in rather than running the bridge as root. If the node reports a busy
port, close the other reader. A connected but stale node may still be waiting
for Arduino setup, or may have the old firmware/device selected.

Xbox driving still uses the original Python script. Set its `ROVER_IP` to the
IP shown by the rover and keep UDP port 4210. This bridge does not change the
existing mode/standby behavior or add a motor-command timeout. Loss of USB
telemetry does not stop the motors. Initial encoder checks should be performed
with the drivers in standby, turning each wheel by hand.

## Wire protocol and ROS contract

One ASCII CSV record every 100 ms, when USB can accept it:

```text
MOTOR1,uptime_ms,fl_count,rl_count,fr_count,rr_count,throttle,steering,enabled
MOTOR1,12345,10,20,30,40,180,-45,1
```

The decoder accepts LF and CRLF, ignores debug lines, retains partial records,
and discards oversized lines through the next newline. The limit is 256 bytes
per line. It checks field count, decimal syntax, integer ranges, and that
`enabled` is exactly `0` or `1`. No commands are sent over USB.

`/rover/motor_telemetry` uses `mini_rover_interfaces/msg/MotorTelemetry`:

| Field | Type | Meaning |
| --- | --- | --- |
| `stamp` | `builtin_interfaces/Time` | Host ROS receive time, not device sample time |
| `uptime_ms` | `uint32` | Arduino uptime in milliseconds |
| `fl_count`, `rl_count`, `fr_count`, `rr_count` | `uint32` | Front-left, rear-left, front-right, rear-right pulse counts |
| `throttle`, `steering` | `int32` | Last UDP command, not measured/applied motor output |
| `driver_enabled` | `bool` | Common standby pin is HIGH |

Counters and uptime reset on reboot and wrap modulo 2^32. Values are forwarded
unchanged; this version does not distinguish a reboot from wraparound or derive
velocity. Publishing uses reliable QoS, volatile durability, and queue depth 10.

`/diagnostics` uses `diagnostic_msgs/msg/DiagnosticArray`, reporting:

- ERROR when the port cannot be opened or is disconnected; retry every 2 s.
- WARN while connected and awaiting the first record for less than 1 s.
- STALE after 1 s without a valid record, checked every 0.2 s.
- OK while valid records are arriving.

Diagnostics include valid/invalid/oversized record totals and last-valid age.
Host monotonic time controls retries and freshness, even if ROS simulated time
is paused. ROS time stamps the messages. Partial input and OS-buffered old data
are cleared when reopening a device. No repeated publication of stale data.
One already-buffered hardware/USB record may still arrive after reconnect; the
wire format has no request/response or session identifier to rule that out.

## Tests and hardware acceptance

For the complete ordered bench procedure, expected results, results sheet,
and rollback instructions, see [INTEGRATION_TEST_PLAN.md](INTEGRATION_TEST_PLAN.md).

After building and sourcing the workspace:

```bash
colcon test --packages-select mini_rover_bridge --event-handlers console_direct+
colcon test-result --verbose
```

Tests cover framing at every split boundary, debug text, multiple records,
invalid/oversized data, integer limits, and raw counter reset/wrap values. ROS
integration tests use real Linux pseudo-terminals and pySerial to verify named
fields, receive timestamps, no outgoing bytes, stale diagnostics, device
absence, unplug/reconnect, and clearing partial data.

Physical acceptance is still required:

1. Confirm the UNO R4 WiFi board and pin map, upload, and discover the new port.
2. With drivers in standby, rotate each wheel separately; verify only its count
   changes and agrees with the OLED encoder page. Test rotation both ways.
3. Check approximately 10 Hz with `ros2 topic hz`. The main loop's other work
   can lower the rate; samples are deliberately skipped under USB backpressure.
4. Verify Wi-Fi Xbox commands still work and command/enable telemetry follows
   the firmware's existing behavior.
5. Boot with Wi-Fi unavailable and with the OLED disconnected; telemetry should
   still arrive after setup. Unplug/replug USB and reset the board; the reader
   should recover without manual restart when its configured device reappears.
6. Pause/close the serial reader and confirm drive responsiveness is unaffected.

The ROS packages are new Apache-2.0 code; imported rover sources retain their
upstream licensing status. See `ros2_ws/LICENSE` for the new packages' license.
