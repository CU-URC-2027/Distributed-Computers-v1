> Historical pre-v2 document. For the active OPEN_LOOP_PWM contract and tests, use [INTERFACE_V2.md](INTERFACE_V2.md), [README.md](README.md), and [VALIDATION_V2.md](VALIDATION_V2.md). Older speed/encoder-enable assumptions do not apply to this milestone.

# Arduino + ROS 2 motor telemetry integration test plan

**Goal:** show that the existing Arduino motor-control code still works while
four wheel encoder counts and command/status values reach ROS 2 over USB-C.

**Test order:** confirm the existing firmware → build → upload → inspect USB
data → check ROS → test each encoder → test Wi-Fi driving → test recovery.

This is a procedure to execute, not a report of completed hardware tests.
Software build and simulation results are recorded separately in VALIDATION.md.

## 1. Preparation and integration baseline — T01

**Equipment:** UNO R4 WiFi rover, Linux computer with ROS 2 Jazzy, USB data
cable, Xbox controller, existing rover power supplies, and a support that keeps
all wheels off the bench for powered tests.

Start with motor power disconnected, retaining the existing approved power
arrangement for the Arduino and encoders. Do not assume USB can power the
motors. For powered tests, keep a physical motor-power disconnect within reach.
The current code has no drive-command timeout: stopping Python or ROS, or
unplugging USB, is not a motor-stop command.

1. Save the **known-working Arduino source folder** and its pin map somewhere
   separate before uploading anything. Save its board/core/library versions
   and current Xbox script too. This means backing up source you already have;
   uploading does not retrieve the original sketch source from the board.
2. Confirm the actual board says **UNO R4 WiFi**.
3. Compare the current source with the supplied v6 baseline. If your current
   sketch has newer changes, merge the telemetry additions into a copy of that
   sketch before proceeding. Do not discard existing changes just to match this
   test plan.
4. Confirm these encoder connections against the wiring:

   | Wheel | Firmware name | Arduino digital pin | ROS field |
   | --- | --- | --- | --- |
   | Front left | `PIN_ENC_FL` | D0 | `fl_count` |
   | Rear left | `PIN_ENC_RL` | D9 | `rl_count` |
   | Front right | `PIN_ENC_FR` | D11 | `fr_count` |
   | Rear right | `PIN_ENC_RR` | D13 | `rr_count` |
   | Shared motor enable | `PIN_MOTOR_STBY` | D8 | `driver_enabled` |

5. Record how the working firmware behaves: encoder page, OLED buttons, LED
   modes, and controller inputs. If an encoder already fails on the original
   firmware, record that fault; integration cannot establish that sensor works.

**Pass:** board, source baseline, and pin map are identified; a restoreable
working sketch is available. Resolve mismatches before flashing.

### What is being integrated

The supplied `firmware/mini_rover_mule/` folder is a **complete modified sketch**,
not a second program to append to the original. Keep one `setup()` and one
`loop()`. Its additions are:

| Area | Integration change |
| --- | --- |
| USB console | Add `usb_console.h` and `UsbConsole RoverSerial`; route sketch serial input/output through it at 115200 |
| Encoder counters | Use `volatile uint32_t`; copy all four counters with interrupts briefly masked and then restored |
| Telemetry | Call `sendMotorTelemetry()` every loop; it samples every 100 ms and skips a write if USB cannot accept it |
| Wi-Fi setup | One connection attempt with a 10-second connection timeout, then continue even if offline |
| UDP | Only poll an initialized UDP socket; reserve one byte for the packet's null terminator |
| OLED | Display the IP cached at startup; telemetry runs independently of the selected page/display connection |
| Credentials | Read the existing network settings from local `arduino_secrets.h` |

The motor mixing, PWM commands, LED command meanings, and encoder wiring are
not redesigned. USB uses native `SerialUSB` on the same USB-C connector, so its
Linux device identity can change after uploading. Keep the normal UNO R4 WiFi
board selection; do not change the `NO_USB` build define or WiFiS3 UART mapping.

## 2. Build before touching hardware — T02

**Arduino:** open the supplied sketch in Arduino IDE, select UNO R4 WiFi,
confirm the local `arduino_secrets.h` contains your intended network settings,
and use **Verify**. Keep the `.ino`, both support headers, and secrets header
together in the sketch folder.

The tested core is Arduino Renesas UNO **1.6.0**. Library versions are listed
in VALIDATION.md. The display library is **Adafruit SH110X**, not the older
SSD1306 library named in the upstream README.

**Linux:** run these in a terminal:

```bash
cd '/home/liamolczak/Documents/ChatGPT/Deep dive into motor prototype/ros2_ws'
source /opt/ros/jazzy/setup.bash
colcon build --symlink-install
source install/setup.bash
colcon test --packages-select mini_rover_bridge --event-handlers console_direct+
colcon test-result --verbose
```

**Pass:** Arduino verification and both ROS packages build; all 23 existing
tests pass. These tests cover malformed/partial input, stale status, and
simulated disconnect/reconnect. They do not prove the physical encoders work.

## 3. Upload and identify the running USB device — T03

1. Stop the Xbox script and ROS bridge. Close Serial Monitor. Keep motor power
   disconnected for this test.
2. Connect USB and upload the verified sketch using Arduino IDE.
3. Allow setup to finish. Offline startup includes a 10-second Wi-Fi connection
   timeout plus other initialization/modem overhead; telemetry is not immediate.
4. Inspect the device **after the uploaded sketch starts**:

   ```bash
   ls -l /dev/serial/by-id/
   ```

   If absent, inspect available ports and the actual device identity:

   ```bash
   ls /dev/ttyACM* /dev/ttyUSB*
   udevadm info --query=property --name=/dev/ttyACM0
   ```

   Substitute the actual port for `/dev/ttyACM0`; missing-device messages from
   either glob simply mean no matching port exists.
5. Record the running firmware's device path. Set it in each terminal that
   needs serial access, replacing this placeholder:

   ```bash
   export ROVER_PORT='/dev/serial/by-id/REPLACE_WITH_ACTUAL_DEVICE'
   ```

**Pass:** upload succeeds and the running sketch exposes a serial device.
If it fails to enumerate, stop here. Check the cable, board selection, upload
result, and native USB setup. Do not change encoder wiring to fix USB discovery.

## 4. Verify raw Arduino telemetry first — T04

Run one serial reader, with the ROS bridge still stopped:

```bash
python3 -m serial.tools.miniterm "$ROVER_PORT" 115200
```

Observe for 30 seconds without typing commands. Example record:

```text
MOTOR1,12345,10,20,30,40,0,0,0
```

Its fields are: marker, uptime in milliseconds, FL/RL/FR/RR counts, last
throttle, last steering, and motor-enable flag. Blank lines and startup/debug
text are allowed. Early startup messages may have been dropped before the
reader connected; they are not required for this test.

**Pass:** intact `MOTOR1` records arrive regularly, uptime advances, stationary
encoder counts remain stable, and `enabled` is `0` after startup with no UDP
mode commands. Throttle and steering should initially be zero. Observe the
four counters rather than requiring a particular initial count.

Exit miniterm with **Ctrl+]** before starting ROS. Serial Monitor, miniterm,
and the ROS bridge must not compete for the same port.

## 5. Confirm ROS topics, timing, and diagnostics — T05

In **each ROS terminal**, run:

```bash
cd '/home/liamolczak/Documents/ChatGPT/Deep dive into motor prototype/ros2_ws'
source /opt/ros/jazzy/setup.bash
source install/setup.bash
```

In terminal A, set `ROVER_PORT` as above and launch:

```bash
ros2 launch mini_rover_bridge motor_telemetry.launch.py port:="$ROVER_PORT"
```

In terminal B, inspect values:

```bash
ros2 topic echo /rover/motor_telemetry
```

In terminal C, inspect connection status:

```bash
ros2 topic echo /diagnostics
```

Measure frequency for 60 seconds in another terminal, or stop terminal B's
echo with Ctrl+C and use it for:

```bash
ros2 topic hz /rover/motor_telemetry
```

**Pass criteria:**

- Fields have the same meanings and wheel order as the raw serial data.
- Host timestamps advance; they are not equal to Arduino uptime.
- Diagnostics for `mini_rover/usb_motor_telemetry` settle to level **0 / OK**.
- Use **9–11 Hz averaged over 60 seconds** as this integration test's target
  band around 10 Hz, excluding startup and deliberate fault tests.
- No unexpected stale intervals or increasing invalid/oversized record counts
  in the healthy steady-state test.

If the rate is lower, record the actual rate and OLED page and investigate loop
timing/USB load. Do not label a lower rate a pass merely because values arrive.

## 6. Check each physical encoder — T06

Keep motor power disconnected and the encoders powered through their normal
logic supply. Use the OLED encoder page and ROS topic together.

1. Record all four starting counts.
2. Gently turn only the front-left wheel, if the gearbox permits manual turning.
   Do not force a gearbox that resists turning; defer that wheel to a controlled
   powered check with the wheels raised.
3. Stop the wheel and wait a second for the OLED and ROS readings to catch up.
4. Confirm only `fl_count` changed and compare the settled count with the OLED.
5. Repeat for rear left, front right, and rear right.
6. Turn each tested wheel in the opposite direction. Its count must still
   increase: this firmware reads one encoder phase and does not measure direction.
7. Change OLED pages and repeat a count check. ROS updates must continue on
   every page.

| Wheel | Start count | End count | Correct ROS field changed? | Settled OLED agrees? | Result |
| --- | --- | --- | --- | --- | --- |
| FL | | | | | Not run |
| RL | | | | | Not run |
| FR | | | | | Not run |
| RR | | | | | Not run |

**Pass:** every encoder produces changes in its corresponding field and no
unmoved wheel produces unexplained changes. An OLED/ROS match alone is not
proof of correct hardware counting because both use the same Arduino counters.
Visible count changes for actual movement are required.

If a count stays zero, inspect encoder power, common ground, signal wiring,
pin-map agreement, and interrupt operation before investigating ROS parsing.
If the wrong field changes, check wheel identification and wiring first.

## 7. Check command/mode compatibility, then powered driving — T07

First keep motor power disconnected. Set the original Xbox script's `ROVER_IP`
to the rover's current displayed IP, confirm port 4210, and run the script in
a separate terminal. Keep the stick centered initially.

| Input through the Xbox/UDP script | Expected result |
| --- | --- |
| X press | Blue LED; `driver_enabled: true` |
| Centered stick | `throttle: 0`, `steering: 0` after updates |
| Forward/backward stick | Positive/negative throttle according to the configured controller axes |
| Left/right stick | Steering changes sign according to the configured axis |
| B press | Red LED; driver remains enabled; UDP drive packets are still processed |
| A press | Flashing green; `driver_enabled: false`; UDP handler disables drivers and calls `stopMotors()` |

Confirm physical button/axis mappings before applying motor power. The
existing host treats button indices 2 and 3 as X, so another physical button
may also select teleoperation. Log mapping problems separately from USB faults.

**Important existing behavior:** serial `X/B/A` and OLED mode buttons only
change LED mode; they do not perform the same standby-pin actions as the UDP
mode commands. Do not use serial `A`, an OLED button, closing ROS, or Ctrl+C in
the Xbox script as an emergency stop. AUTO is a status mode here, not a new
autonomous-driving implementation.

After the unpowered checks pass:

1. Center the stick, send UDP A to disable drivers, stop the controller script,
   and secure the rover with all wheels clear of the bench.
2. Restore normal motor power. Keep the physical motor-power disconnect ready.
3. Restart the Xbox script with the stick centered, then press X.
4. Apply a small command for 1–2 seconds, return the stick to center, and confirm
   wheel movement stops. Repeat briefly in reverse and with gentle steering.
5. Observe encoder changes while wheels move. Compare commands in the host
   display with ROS values; allow for normal sampling delay.
6. At a small command, use UDP A to test driver disable. Confirm the flag goes
   false and motion stops after mechanical coast-down. Remove motor power if
   stopping does not match the expected behavior.
7. Finish centered and disabled; remove motor power before further fault tests.

**Pass:** existing drive behavior is retained, encoder feedback follows wheel
movement, and healthy USB telemetry continues near the T05 target rate.
Do not require identical counts on all wheels or interpret command numbers as
measured speed. Counts may continue during coast-down after disabling drivers.

## 8. Independence and recovery tests — T08 through T12

Keep motor power disconnected for these tests. Stop the Xbox script unless a
test explicitly needs LED commands. Power down before disconnecting a sensor.

| ID | Procedure | Expected result / pass criterion |
| --- | --- | --- |
| T08 | Boot with the configured test Wi-Fi network unavailable. Use a network you control; record this test as not run if it cannot be isolated. | USB telemetry starts after setup finishes; no indefinite Wi-Fi wait. Record actual startup time. Restore network and restart Arduino to retry Wi-Fi. |
| T09 | Power off, unplug the OLED from its connector, and restart. | ROS telemetry still arrives; manually changing encoder counts still works. Power off before reconnecting the OLED. |
| T10 | With bridge running, unplug USB for 5 seconds and reconnect; repeat three times. | Bridge reports ERROR and stops publishing new measurements while disconnected; reconnects automatically. Once the same configured device path returns, expect opening within roughly 2–3 seconds plus any Arduino startup delay. |
| T11 | Reset Arduino with bridge running. | Counts and uptime reset; telemetry resumes without restarting the ROS bridge. A brief ERROR or STALE transition is acceptable. No velocity/odometry jump is assessed because none is computed. |
| T12 | Stop and restart the ROS bridge three times while the Arduino stays powered. | Port reopens each time, healthy telemetry returns, and starting/stopping the bridge does not itself enable motors or send commands. |

For T10, distinguish **data disconnection** from **power removal**. If USB is
the Arduino's only supply, unplugging it reboots the board and resets counters;
that is expected. With its normal independent logic power, unplugging USB
should not reset counters. Use the running sketch's stable device path; if
Linux assigns a different path, record it and correct the configuration.

Do not expect an ERROR diagnostic if the entire ROS bridge is stopped: no
process remains to publish one. STALE means the bridge is still connected but
has received no valid record for at least one second; the automated serial
tests cover this separately from physical unplugging.

## 9. Reader stopped / USB backpressure — T13

**Purpose:** check that an attached computer which stops reading does not
freeze the Arduino loop. Keep motor power disconnected.

1. Stop ROS and all other serial readers. Set `ROVER_PORT` in this terminal.
2. Open the port and deliberately leave it unread for two minutes:

   ```bash
   python3 - "$ROVER_PORT" <<'PY'
   import sys
   import time
   import serial

   with serial.Serial(sys.argv[1], 115200, timeout=0, exclusive=True):
       print('Port open, DTR asserted; not reading for 120 seconds.', flush=True)
       time.sleep(120)
   print('Port closed. Restart the ROS bridge.')
   PY
   ```

3. During the pause, repeatedly change OLED pages and verify they respond.
   If Wi-Fi is available, use the Xbox script with centered stick to change
   LED modes. Keep motor power disconnected and finish with UDP A, then stop
   the Xbox script.
4. After the reader closes, restart the ROS bridge and repeat the T05 checks.

**Pass:** UI/LED updates remain responsive and telemetry resumes after reopening
the port. Skipped telemetry during the pause is expected; it is not replayed
from an application queue. A small amount already buffered by USB/OS may arrive.

Record this as an **unread-host test**. A timed pause alone cannot prove that
every USB/OS buffer filled. Proving the exact full-buffer branch would require
additional firmware instrumentation, which this procedure does not install.

## 10. Record a final run and decide acceptance — T14

Record a five-minute healthy session after restoring normal Wi-Fi and OLED
configuration. Include stationary time and brief supported-wheel movements
using the T07 procedure. Use a new output name each run:

```bash
cd '/home/liamolczak/Documents/ChatGPT/Deep dive into motor prototype'
mkdir -p artifacts/integration
ros2 bag record -o artifacts/integration/run_01 /rover/motor_telemetry /diagnostics
```

Source the ROS workspace first, as in T05. Stop recording with Ctrl+C. Inspect
the result:

```bash
ros2 bag info artifacts/integration/run_01
```

**Pass:** the bag includes both topics; no unexplained restart, sustained rate
drop, stale interval, or malformed-record increase occurs during the healthy
run. Document intentional fault intervals separately if recording those tests.

### Results sheet

Record the firmware source/version, board/core versions, Linux USB path,
test date, operator, and bag/log locations alongside these results.

| Test | Result: Pass / Fail / Not run | Observation / evidence |
| --- | --- | --- |
| T01 Baseline and pin map | Not run | |
| T02 Firmware/ROS builds and 23 tests | Not run for this integration session | Prior software results: VALIDATION.md |
| T03 Upload and USB discovery | Not run | |
| T04 Raw serial records | Not run | |
| T05 ROS fields, rate, diagnostics | Not run | |
| T06 All four physical encoders | Not run | |
| T07 Existing Wi-Fi drive behavior | Not run | |
| T08 Wi-Fi unavailable | Not run | |
| T09 OLED disconnected | Not run | |
| T10 USB reconnect ×3 | Not run | |
| T11 Arduino reset | Not run | |
| T12 ROS restart ×3 | Not run | |
| T13 Unread host | Not run | |
| T14 Five-minute recorded run | Not run | |

Call the milestone **fully integrated** only when T01–T14 pass. If a test
cannot be performed, label the result partial and list the remaining test;
do not count simulation or compilation as a physical-test pass.

## Failure isolation and rollback

| Symptom | First place to look |
| --- | --- |
| No USB device | Data cable, upload result, board selection, native USB enumeration |
| Device exists, no `MOTOR1` | Wrong port/sketch, startup still running, another serial reader |
| Raw records good, ROS absent | Workspace sourcing, port parameter/permissions, bridge logs |
| One encoder unchanged in both OLED and ROS | Encoder power/signal, wiring, interrupt pin configuration |
| OLED changes, corresponding ROS field does not | Wire field order, parser, selected board/serial device |
| Wrong wheel label | Physical identification and pin-map agreement |
| USB works, Wi-Fi does not | Credentials, initial connection result, displayed IP, host IP/port |
| Everything freezes when host stops reading | Inspect every USB writer for bypassing `UsbConsole` and its capacity check |

To roll back: disconnect motor power, stop the Xbox script and ROS bridge,
close all serial readers, and upload the saved known-working sketch with its
original support files and board settings. If the IDE cannot find the sketch
port, double-tap RESET and select the bootloader port. After restoration,
rediscover the serial device and rerun the original baseline checks before
applying motor power. Keep the integration source and failure notes for diagnosis.
