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
