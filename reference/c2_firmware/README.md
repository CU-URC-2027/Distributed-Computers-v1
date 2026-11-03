# Companion firmware and chassis protocol

## What to upload

- `mini_rover_c2/mini_rover_c2.ino`: Uno R4 WiFi chassis sketch. Keep C2Guard.h,
  pin_map.h, c2_config.h and wifi_credentials.h together in that directory.
- `gps_uno_bridge/gps_uno_bridge.ino`: separate ATmega328 Uno GPS-to-USB sketch.
  It is NOT the R4 motor firmware.

Set the router SSID/password locally in wifi_credentials.h. GPS is off by default.
The optional R4 GPS setting requires physical rewiring, described in
[GPS_HOME_TEST.md](../GPS_HOME_TEST.md). No firmware is uploaded by launching C2.

The chassis sketch derives from Matthew's v6 at
[mini-rover-prototype](https://github.com/mafl8398/mini-rover-prototype), retrieved
2026-10-24. Reference Git blob IDs:

| Upstream file | Blob |
|---|---|
| mini_rover_mule.ino | d4c12238482bdeb37ddabaa2452cc4e1728a190b |
| pin_map.h | 8609fc513cbc7e9e21024819c99fd6e31397bf72 |
| teleop_controller.py | 395869c3c1ef725dfa1c6f29211fc3aa637d676b |
| README.md | 900bfe37c0df06eee7f57ef16aaf09b9ba16a58f |

Motor direction/PWM/STBY pins, left/right mixing, OLED, IMU and LED routines are
retained. Changes replace the permissive UDP parser, introduce guarded state and
telemetry, hold STBY low from the start of setup, remove legacy motion/enable
acceptance, and add the opt-in GPS relay. Encoder availability is checked at startup:
unsupported interrupts report null. Battery display logic is inherited and still
needs calibration; battery/IMU are not yet exported as C2 telemetry.

## C2V1 command frame

UDP port 4210. ASCII, one packet per datagram, under 128 bytes, optionally newline:

```text
C2V1,<session>,<sequence>,<epoch>,<operation>,<throttle>,<steering>\n
```

Session: 16 lowercase hex characters, newly generated per connection. Sequence:
positive uint32, strictly increasing for accepted commands; reconnect before wrap.
Epoch: MCU state-generation counter from returned state. Throttle/steering: signed
integer PWM units, both resulting wheel commands bounded to ±64:
`left = throttle + steering`, `right = throttle - steering`.
The original two-number encoding is preserved as values inside this envelope.
No raw legacy drive packet or MODE:TELEOP can enable this firmware.

| Operation | Behavior |
|---|---|
| HELLO | Establish disabled owner/session; no enable. New owner requires disabled MCU and previous-owner inactivity ≥1 s |
| PING | Acknowledge communication only; does not refresh drive timeout |
| ARM | Current epoch, disabled, no STOP latch, zero values; starts enabled with zero output |
| DRIVE | Current session/epoch, enabled, no STOP latch; validates range and refreshes local drive timer |
| DISARM | Zero PWM and STBY low; new Enable required |
| STOP | Same, plus latched stop |
| RESET | Disabled/current epoch only; clears latch and advances epoch, remains disabled |

All non-DRIVE commands must have zero throttle/steering. Invalid, duplicate,
out-of-order, wrong-session and old-epoch motion is rejected. Timeout is checked
before accepting arriving commands, so a delayed DRIVE cannot revive an expired
lease. PING/HELLO/GPS do not refresh drive time. A legacy MODE:ARRIVED packet is
accepted only as a stop; other old commands are ignored.

The UI requires neutral input before ARM. Receiver ARM contains no motion. After
acknowledgment, permitted DRIVE is sent about 20 Hz from the GUI update loop. A
background thread never repeats the last drive value, so a frozen UI stops sending.
ARM/DRIVE expiry is 250 ms in C2Guard; each enabled-to-disabled transition changes
the epoch. STOP reset does not enable. A connection fault requires deliberate reconnect.

This is a trusted bench-LAN protocol, not an authentication/security design. UDP
is lossy and may reorder; the envelope addresses accidental stale commands, not
hostile traffic. Session/epoch are not cryptographic anti-replay across MCU reboot.
Use a board boot nonce, authentication and a reviewed ownership policy before
extending the protocol beyond the isolated prototype network.

## Return state

The receiver sends JSON after accepted commands and periodically (~100 ms):

```json
{"v":1,"type":"state","fw":"c2-guard-v1","session":"0123456789abcdef",
 "seq":12,"epoch":2,"state_id":18,"enabled":false,"stopped":false,
 "throttle":0,"steering":0,"encoders":[32,null,31,null],
 "reason":"DISARMED","watchdog_ms":250}
```

Counts are FL/RL/FR/RR uint32 or null. They do not include direction, calibrated
speed or validity of wiring. Null means interrupt unavailable, not measured zero.
The UI validates source endpoint, session, known acknowledged sequence, monotonically
increasing state ID, types and bounds. It requires both a recent reply and a recent
acknowledged outgoing command; repeated old acknowledgments cannot keep it ready.
After ~350 ms without such feedback, the UI faults/disarms and attempts STOP.
A separate receiver timeout remains essential if the return path or PC fails.

`enabled` and `throttle/steering` describe the firmware's applied software state
following its GPIO writes. They do not verify driver power, current or actual motor
motion. The displayed age is time since receipt. Internal acknowledgment turnaround
includes firmware processing and is not a precise radio-only RTT.

With GPS enabled, additional `{v:1,type:"gps_nmea",session:...,nmea:"$...*xx"}`
packets relay Serial1 lines. They never count as drive acknowledgments. GPS validity
and NMEA checksum validation occur in the Python parser. This version has no sensor
capture timestamp in its relay envelope, so latency analysis requires a later change.

## Limitations to verify physically

The watchdog runs cooperatively. Blocked peripheral calls can delay enforcement;
there is no independent hardware watchdog/relay implementation in this release.
Measure STBY/PWM stop timing under network loss and disconnected peripherals.
Startup and reset require hardware STBY biasing as well as firmware. Do not confuse
LED mode (which serial/buttons can change) with verified drive enable; use STBY/state.

The default stock map's rear encoders on D9/D13 have no interrupt support in the
tested R4 core 1.6.0. The firmware detects this and reports unavailable. Forward
counts need a physical pulse test too. A four-wheel odometry remap is future work.

## Tests and compilation

`tests/guard_test.cpp` tests the same C2Guard.h used by firmware, including malformed
packets, ranges, sequence/epoch, owner replacement, STOP/reset, timeout before a
late packet and millis rollover. Build with C++11, e.g.:

```text
g++ -std=c++11 -Wall -Wextra tests/guard_test.cpp -o guard_test
```

Arduino build targets: `arduino:renesas_uno:unor4wifi` and `arduino:avr:uno`.
R4 libraries: Adafruit SH110X, NeoPixel, MPU6050 and dependencies. The GPS Uno bridge
uses core SoftwareSerial only. See [TEST_RESULTS.md](../TEST_RESULTS.md) for exact
build/test results; compilation is not hardware validation.
