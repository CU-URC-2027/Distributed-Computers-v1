# Rover interface agreement v2 — OPEN_LOOP_PWM

This is the implementation contract for the GUI → OC → USB-C UNO R4 milestone.
The supplied 27 October interface review takes precedence over the older simulated
speed-control migration. Physical motion has not yet been verified.

## Source ownership and baseline

The active host workspace is `/home/liamolczak/weeny_ws`; the firmware source is
`/home/liamolczak/mini-rover-prototype/mini_rover_mule`. The similarly named source
under the ChatGPT document workspace and the older firmware copy in weeny_ws are
not deployment sources. Pre-change archives and SHA-256 manifest are stored at
`/home/liamolczak/rover-backups/20261028T164340Z` (including firmware Git state).

Coordinator owns shared Python contracts/protocol, ROS interfaces, this document,
shared fixtures, end-to-end tests and validation evidence. C2 owns mini_rover_c2
and control/c2.py. OC owns mini_rover_oc, control/oc.py and serial_port.py.
Arduino owns the actual sketch, its build/native tests, control/arduino.py and
mini_rover_sim. Shared changes require coordinator review and notification to all
three implementers. New subsystem tests live in separately owned files; agents
must not overwrite another component's work.

## Existing interfaces and deliberate migration

The old ROS v1 route used DriveIntent forward_mps/turn_radps, four rad/s targets,
wall-clock input age, and invalid encoders as an enable blocker. The actual v6.1
sketch instead accepted single-character WASD and UDP commands, with text-only
telemetry and no motion watchdog. JSON must never be sent to that old sketch.
Version 2 rejects v1 rather than interpreting physical speed as PWM.

## ROS surface

Namespace `/rover1`; domain 92; localhost discovery for the first milestone.
Topics `c2/drive_intent`, `control/state`, `chassis/state`; services
`control/claim`, `control/set_enabled`, `control/stop`, `control/reset_stop`.
Topic QoS: BEST_EFFORT, VOLATILE, KEEP_LAST depth 1, lifespan 250 ms.
Services use ROS service defaults plus application confirmation and freshness.
Exact field definitions are in mini_rover_interfaces and the immutable Python
DriveIntent contract. State emits at 20 Hz; chassis only for new MCU samples.

C2 session_id is a UUID hex process identity; command_id increases for every new
input sample and never resets on reclaim. OC boot_id and generation identify
receiver authority. OC issues a challenge_seq in state and accepts it for 200 ms
on its monotonic clock. Session, generation, challenge, input source and command
sequence all have to match before renewing the 250 ms input lease. Audit stamps
are wall-clock values only; they are not used to compare clocks across computers.
Claim requires a disabled MCU and a unique request; enable/reset also echo current
OC identity, generation and challenge. Only manual GUI input is supported for
physical drive. Switching owners requires disarm and neutral input.

Enable requires neutral controls and released hold. A confirmed service response
means OC accepted an operation, not that MCU enabled. Enable/reset are sent once,
with matching MCU acknowledgment/state required within 350 ms; timeout cancels
authority and requires a new explicit operation. Duplicate request identities
never repeat state changes. STOP/disarm preempt pending enable/reset operations.
Hold release requests zero while armed. Focus loss, input expiry, disarm, STOP,
restart and connection loss withdraw authority. STOP latches; reset leaves disabled.

## Units, mixer and final outputs

Mode is exactly `OPEN_LOOP_PWM`. Throttle and steering are finite normalized
fractions in [-1,1]; positive means forward and left/CCW respectively. OC computes
left=(throttle-steering)*limit and right=(throttle+steering)*limit, then divides
both by max(1,max(abs(left),abs(right))/limit). Round final values symmetrically,
half away from zero. Requested float PWM and limited integer PWM stay distinct.
Limit defaults to 63 and cannot exceed 63 of 255. No value is a measured speed.

Arduino independently rejects out-of-contract values. FL/RL share left output;
FR/RR share right output. Side wire targets avoid pretending there are four
independent actuators. Motor polarity is a board mapping, to be verified unpowered
and with raised wheels. Disabled electrical state: STBY low, PWM zero, direction
outputs low. This does not establish mechanical stopping time. Armed zero uses
zero PWM and low direction signals. This milestone has no acceleration ramp.

## USB command grammar

115200 baud, ASCII JSON object per newline, at most 2048 bytes INCLUDING newline.
No unknown/duplicate fields, boolean-as-integer, nonfinite number, unsupported
version/mode, embedded command language, or out-of-range integer is accepted.
Identifiers are 32 lowercase hexadecimal characters. Optional unbound/source
identifiers may be empty where specified. Counters are exact uint64; uptime/counts
are uint32. Fixed nesting and buffer limits apply. Invalid/oversized input is
consumed through newline and never renews permission.

A command has exactly:
`v,type,mode,op,boot_id,session_id,seq,generation,sample_seq,command_id,source_session,targets`.
`v=2`, `type=command`, `mode=OPEN_LOOP_PWM`; op is one of BIND, ENABLE, TARGETS,
DISABLE, STOP, RESET, PING. `targets={left:int,right:int}` within [-63,63].
Only TARGETS may carry nonzero targets; it requires nonzero command_id and source
session. Non-target operations use zero targets. Sequence/generation/sample_seq
are positive. boot_id must match the MCU. session_id is the OC serial-session UUID.
The MCU sample_seq token expires 200 ms after its local issue time.

BIND is disabled-only and establishes a new serial owner; changing owner resets
transport/application identities and advances generation. No recovered packet can
revive expired enable authority. Duplicate/lower serial sequences do not renew
motion. TARGETS additionally requires newer originating C2 identity within the
current enable operation. ENABLE starts a 250 ms lease; only new valid TARGETS
renew it. PING and status do not. Firmware checks expiry before handling recovered
commands. STOP and DISABLE revoke outputs immediately; RESET clears STOP while
remaining disabled. Stale generations cannot enable/reset/drive.

Feedback has exactly:
`v,type,mode,boot_id,session_id,generation,sample_seq,ack_seq,last_applied_seq,source_session,last_applied_command_id,uptime_ms,enabled,stopped,applied,counts,valid,direction_valid,faults,reason`.
`type=feedback`; applied has two signed side PWM values. counts/valid/direction_valid
have exactly front_left,rear_left,front_right,rear_right. Invalid counts are displayed
N/A, never as measured zero. Single-channel direction_valid is false. No rad/s
measurement or fabricated target error exists. Periodic cadence is approximately
10 Hz, with immediate bounded operation feedback. Serialized maximum-size feedback
must fit 2048 bytes; shared tests check worst permitted values.

ack_seq is the last accepted serial command. last_applied_seq and originating C2
identity advance only on TARGETS after output state is updated. OC tracks sent
TARGETS and validates the MCU-reported mapping before exposing application to C2.
Application proves software output state, not wheel movement. New BIND clears prior
application identity. Invalid/unknown acknowledgments cannot prove application.

Fault objects have exactly source (MCU/OC/C2), code (uppercase token up to 32),
severity (WARNING/ERROR), latched (bool), command_id (uint64), time_ms (uint32,
source clock). At most four current MCU faults fit the wire record. All four
validity flags remain authoritative even with compact faults. Missing encoders
are warnings. Historical first-fault evidence in OC includes originating session
and survives later STOP traffic; it is separate from active inhibiting faults.

## Freshness and provenance

C2 input / MCU drive expiry: 250 ms. MCU feedback / C2 state expiry: 350 ms.
OC state may remain fresh while MCU/sensor state is stale. C2 adds elapsed local
receipt time to downstream mcu_age_ms and rejects duplicate/reversed samples;
unsynchronized transit delay is unknown, not zero. MCU capture time is explicitly
uptime in the MCU clock domain. C2 rejects old OC state sequences and disarms on
changed OC boot/generation or stale state. Hardware and simulated launches are
separate; hardware never starts a simulator and every chassis message carries
truthful provenance. Serial reconnect alone never enables.

## Firmware build and physical gates

UNO R4 WiFi, arduino:renesas_uno:unor4wifi, installed core 1.6.0. Native C++ protocol
and state-machine harness uses the same source as the board. Boot identity uses
checked hardware RNG; failure leaves outputs disabled with BOOT_ID_UNAVAILABLE.
The motor-only build does not execute Wi-Fi/UDP, keyboard, OLED, GPS, sonar, IMU or
NeoPixel tasks. Bounded USB work must not starve watchdog checks.

Operator confirmed pin map and motor power isolation on 29 October; output polarity
and waveforms still require measurement: left DIR D2/D4 PWM D3; right DIR D6/D7 PWM
D5; STBY D8. Encoders FL=A2, RL=D9, FR=D11, RR=D13. Only supported counting inputs
are enabled; unsupported/unverified measurements remain visibly unavailable.
Rear rewiring, signed calibration and WHEEL_SPEED mode are deferred.

Do not declare physical acceptance from simulation. Motor supply must be disconnected
for wiring/protocol/STBY/PWM checks. Then use raised wheels, a reachable power cutoff,
and the unchanged PWM ceiling. Save command/feedback identities and timing logs;
record electrical shutdown separately from physical response. Powered tests remain
pending until the setup is verified by the operator.
