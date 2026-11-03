# Arduino v2 open-loop acceptance plan

This replaces the earlier speed-control migration gates. The supplied interface
agreement permits bounded open-loop driving with unavailable encoders shown as
warnings. Signed-speed calibration is not a prerequisite for this milestone.

## Stage A — reproducible software

- Preserve baseline source and hashes; build all ROS interfaces and packages.
- Compile the actual UNO R4 sketch with core 1.6.0; preserve compiler/build identity.
- Pass shared schema/mixer vectors, native firmware parser/state tests, Python
  controller tests, GUI hold/focus tests and ROS/PTY end-to-end fault scenarios.
- Maximum-size feedback fits the 2048-byte frame; integer IDs above 2^53 retain
  exact values; malformed/oversized input and legacy WASD characters cannot drive.
- Hardware launch must contain no simulator. No physical port is used by tests.

## Pin mapping and rewiring

The active pin configuration is a compile-time header in the actual firmware repository:

```text
/home/liamolczak/mini-rover-prototype/
  mini_rover_mule/pin_map.h
  mini_rover_mule/mini_rover_mule.ino
```

Edit the header inside `mini_rover_mule/`, beside the sketch. The sketch includes it with `#include "pin_map.h"`, and `scripts/build_firmware.sh` compiles that sketch directory. The root-level `mini-rover-prototype/pin_map.h` is historical and is not this build's input. The copies under `weeny_ws/firmware/` and `weeny_ws/reference/` are also historical. The Distributed Computers repository contains those references, not the active UNO R4 firmware repository.

### Current motor and encoder assignments

These are software assignments, not a new claim that wiring has been physically verified. Numeric values such as `2` identify the board pin D2, not a connector position; `A2` identifies the board pin labelled A2.

| Signal | Definition to edit | Current pin |
|---|---|---|
| Left direction input 1 | `PIN_LEFT_DIR1` | D2 |
| Left direction input 2 | `PIN_LEFT_DIR2` | D4 |
| Left PWM | `PIN_LEFT_PWM` | D3 |
| Right direction input 1 | `PIN_RIGHT_DIR1` | D6 |
| Right direction input 2 | `PIN_RIGHT_DIR2` | D7 |
| Right PWM | `PIN_RIGHT_PWM` | D5 |
| Driver standby | `PIN_MOTOR_STBY` | D8 |
| Front left encoder | `PIN_ENC_FL` | A2 |
| Rear left encoder | `PIN_ENC_RL` | D9 |
| Front right encoder | `PIN_ENC_FR` | D11 |
| Rear right encoder | `PIN_ENC_RR` | D13 |

The `PIN_MOTOR_L_IN1`, `PIN_MOTOR_L_IN2`, `PIN_MOTOR_L_PWM` and right-side equivalents are aliases of the direction/PWM definitions above. Leave those aliases referring to the named definitions; change the numeric assignments once. Front and rear motors on each side share one output. Four independent motor PWM outputs would require a firmware and interface redesign.

### How a pin change affects driving

For a wiring-only change, update the matching definition, rebuild and upload the resulting firmware. This mapping is not a ROS parameter or an entry in `config/settings.yaml`. C2 and OC continue sending named left/right PWM commands and need no change when their meaning stays the same.

For example, if the left driver's IN1 and IN2 signal wires are swapped between D2 and D4, the corresponding header assignments become:

```cpp
#define PIN_LEFT_DIR1     4
#define PIN_LEFT_DIR2     2
```

This is an example only; keep the current assignments unless the actual wiring changes. In `mini_rover_mule.ino`, `setup()` configures the selected pins, `applyOutputs()` writes direction and PWM, and `disableOutputs()` lowers standby, PWM and both direction inputs. Positive side PWM sets IN1 high and IN2 low; negative PWM reverses them. If a whole side rotates backward, check motor/driver polarity against this convention rather than changing C2's forward/steering signs. Swapping a side's IN1/IN2 assignments reverses its electrical direction. Confirm both motors on that side behave consistently.

Choose replacement PWM pins that support `analogWrite()` on the UNO R4 WiFi and installed core 1.6.0. Check every proposed pin for duplicate assignments, interrupt capability where needed, board restrictions and physical conflicts. GPS, sonar, OLED and other peripheral names still appear in the header although this motor sketch does not run them; account for those reservations before restoring peripherals. There is no comprehensive automatic pin-conflict check.

### Encoder rewiring needs an additional check

The current sketch attaches rising-edge interrupts only for FL and FR in `setup()`. Their handlers update array positions FL=0 and FR=2. All four telemetry positions are ordered FL, RL, FR, RR. Changing the FL/FR definitions redirects those existing handlers only if the new pins support interrupts on this board/core.

Changing `PIN_ENC_RL` or `PIN_ENC_RR` alone does not add rear counting. Rear support also needs handlers for positions 1 and 3, the matching `encoderSeen` bits 2 and 8, input configuration and interrupt attachment in the sketch, plus tests. Until implemented and verified, keep rear channels invalid. A front channel becomes available only after a pulse is observed; single-channel pulses do not establish direction. Hand-turn each wheel and verify its named channel. Do not infer availability from a header definition.

### Rebuild and recheck after rewiring

With motor power disconnected, record the old/new wiring and header values, then run these software checks from the actual firmware repository:

```bash
cd /home/liamolczak/mini-rover-prototype
./scripts/test_firmware.sh
./scripts/build_firmware.sh
```

These commands test and compile; they do not upload. Use the project's verified v2 upload procedure for the newly built image. The build script requires core 1.6.0 and preserves the native USB configuration; a generic IDE build is not an equivalent replacement. Build identity, pin-header SHA-256 and compile output are recorded in `.build/firmware/build_evidence.json` and `.build/firmware/compile.log` by default. Native tests do not prove pin capability or wiring.

Repeat Stage B's disabled-startup and electrical-output checks, then Stage C's channel checks. Only proceed to Stage D after the rewired setup passes those gates. Record results for the new configuration; earlier physical results do not validate new wiring. The local simulator does not use Arduino pins and cannot validate a rewired board.


## Stage B — setup and board communication, motor supply disconnected

An operator must confirm actual wiring before powered testing. Expected map:
left DIR D2/D4 PWM D3; right DIR D6/D7 PWM D5; STBY D8;
encoders FL=A2, RL=D9, FR=D11, RR=D13. The older sonar map used A2: resolve any
physical conflict. Unsupported rear encoder counting stays invalid.

Identify the actual USB device, ensure one serial owner, upload the verified v2
sketch, and record build hash/core/pin configuration. Do not use the JSON writer
against legacy v6.1 keyboard firmware. Recheck device enumeration after upload.

With motor power disconnected, verify boot/reset PWM zero and STBY low before
initialization; offline boot without Wi-Fi or peripherals; fresh disabled v2
telemetry; distinct boot identity across resets; RNG failure inhibits enable.
Exercise bind, neutral enable, zero, disarm, STOP/reset, invalid frames, stale
challenge/generation, reconnect and blocked USB reader. Observe electrical output
state and loop timing, not merely printed telemetry. No automatic re-enable.

## Stage C — encoder evidence

Hand-turn each available encoder with motor power disconnected. Confirm wheel
label, increasing raw counts, capture sample/time, wrap behavior and validity.
Single-channel counting does not establish direction. Unavailable/unverified
channels show N/A; do not manufacture zero speed or mark absent channels valid.
Rear rewiring and all calibrated speed claims remain deferred.

## Stage D — raised-wheel motor route

With wiring/output checks complete, wheels raised and a reachable motor-power
cutoff, use GUI hold-to-drive at a ceiling of 63/255. Confirm forward/reverse,
left/right, combined commands, paired outputs and polarity. Do not increase the
ceiling automatically if a motor does not move. Correlate C2 command identity,
OC-limited side PWM, MCU-applied identity/PWM, and physical observation.

Release hold → zero. Focus loss/disarm → disabled. STOP → latched disabled.
Reset STOP → disabled. Repeat after killing C2, killing OC, losing serial traffic,
suppressing feedback and restarting MCU. Renewed traffic never restores enable.
Measure MCU electrical shutdown against its 250 ms threshold plus measured bounded
loop interval. Measure physical stopping separately; no guaranteed mechanical
stopping distance/time is inferred from the software watchdog.

## Results

For each case record test ID, source/build hashes, setup/power state, stimulus,
expected/actual behavior, timing evidence and PASS/FAIL/PENDING. Simulation never
passes board or motion gates. Preserve first-fault evidence even after STOP.
Actual results are maintained in VALIDATION_V2.md; physical stages remain pending
until performed with the operator and documented setup.
