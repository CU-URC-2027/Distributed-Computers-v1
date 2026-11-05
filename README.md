# Distributed Computers v1

ROS 2 Jazzy C2, onboard controller, and Arduino simulator source, with editable
local test settings and Word reference guides. The local simulation has been
validated; deployment across two physical computers remains unvalidated.

To reproduce on another Linux computer with ROS 2 Jazzy and the documented Python
and colcon dependencies installed, clone this repository into a ROS workspace:

```bash
git clone https://github.com/CU-URC-2027/Distributed-Computers-v1.git ~/weeny_ws
cd ~/weeny_ws
./scripts/build.sh
./scripts/dcv1 start --gui
```

The detailed local walkthrough is in [distributed computers v1](distributed%20computers%20v1/README.md).
The split deployment commands are in its [network guide](distributed%20computers%20v1/docs/network_nodes.md).
Optional per-computer CPU, memory, ROS timing, fault recording, and a desktop
dashboard are documented in [Performance inspection](PERFORMANCE_INSPECTION.md).
Download the [PDF run guide](output/pdf/performance-inspection-run-guide.pdf) for
setup, hardware/simulator startup, dashboard use, logs, replay, and troubleshooting.
Build outputs, live process records, and raw session logs are excluded from Git.
The actual UNO R4 firmware remains a separate repository, needed only for hardware
work; the included older firmware copies are historical references.

Goal: **GUI C2 → ROS 2 OC on this PC → USB-C UNO R4 → motors → C2 feedback**.

The implementation uses normalized throttle/steering, hold-to-drive, and a hard
ceiling of 63/255 PWM. PWM is not measured speed. Four encoder counters have
independent validity; unavailable encoders warn without blocking open-loop drive.
The authoritative agreement and ownership are in [INTERFACE_V2.md](INTERFACE_V2.md).
For current GUI and simulation CLI commands, use [OC_PROTOTYPE.md](OC_PROTOTYPE.md).
See [VALIDATION_V2.md](VALIDATION_V2.md) for actual results and remaining hardware gates.

## Active source

Host: `/home/liamolczak/weeny_ws`. Firmware:
`/home/liamolczak/mini-rover-prototype/mini_rover_mule`.
Do not flash the older sketch under this workspace's `firmware/` or `reference/`.
Do not connect the JSON OC writer to the old v6.1 firmware: its keyboard parser
can interpret JSON letters as full-power motor commands.

Before this migration, all source/documents and firmware Git state were archived
under `/home/liamolczak/rover-backups/20261028T164340Z`, with SHA-256 hashes.

## Build and simulation

The configurable local simulation tools and Word guides are in
[distributed computers v1](distributed%20computers%20v1/README.md). Run them here:

```bash
cd ~/weeny_ws
./scripts/dcv1 start
./scripts/dcv1 test
./scripts/dcv1 stop
```

Edit `distributed computers v1/config/settings.yaml`; logs are saved under
`distributed computers v1/evidence/`. The existing demo launcher below remains available.

```bash
cd /home/liamolczak/weeny_ws
./scripts/build.sh
./scripts/run_tests.sh
./scripts/run_demo.sh
```

The simulator uses a separate PTY process and is labelled SIMULATED in feedback.
The host needs ROS 2 Jazzy, Python pyserial/pytest/tkinter and a display (or Xvfb
for widget tests). The existing installed dependencies are used.

GUI sequence: Connect/claim, leave input neutral with hold released, Enable, wait
for confirmed MCU enable, enter throttle/steering, then hold the drive button.
Release requests zero. Focus loss disarms. STOP latches; Reset leaves disabled.
Re-enable always requires neutral input and a released hold. Values are fractions
[-1,1]; positive steering turns left. No Xbox or direct UDP path is in this GUI.

C2 shows operator request, OC-limited side PWM and MCU-applied PWM separately.
Applied means output-state update; only observation/sensors can establish motion.
MCU enable and sensor data become unknown/stale when feedback is missing.

## Hardware launch — after firmware/wiring gates

Build the new UNO R4 firmware using the firmware repository's build script and
follow [ARDUINO_MIGRATION_TEST_PLAN.md](ARDUINO_MIGRATION_TEST_PLAN.md). Firmware
upload and actual motor tests are separate from compiling or simulation.
With the verified v2 sketch, motor supply initially disconnected, and the runtime
USB path identified, launch:

```bash
source /opt/ros/jazzy/setup.bash
source /home/liamolczak/weeny_ws/install/setup.bash
export ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST
export ROS_DOMAIN_ID=92
/home/liamolczak/weeny_ws/scripts/run_hardware.sh port:=/dev/serial/by-id/REPLACE_WITH_VERIFIED_DEVICE
```

Hardware launch starts OC and GUI only. It must never substitute a simulator for
missing hardware. The actual device name must be discovered after upload; a
previous `/dev/ttyACM0` name is not proof of device identity.

## Board route check

After verified v2 upload, this command records the actual OC→C2 hardware telemetry
and claims only neutral/disabled control:

```bash
source /opt/ros/jazzy/setup.bash
source /home/liamolczak/weeny_ws/install/setup.bash
python3 /home/liamolczak/weeny_ws/scripts/check_hardware_route.py \
  --port /dev/serial/by-id/REPLACE_WITH_VERIFIED_DEVICE \
  --output /home/liamolczak/weeny_ws/validation/board-check
```

The optional `--unpowered-control-checks --motor-supply-disconnected` flags exercise
bounded command acknowledgments, STOP/reset and C2 silence on an unpowered bench.
They are not a physical driving interface or proof of electrical/mechanical action.
The normal operator interface is the GUI hold-to-drive control.

A hardware reconnect issue is under investigation: native USB works after upload,
but a later reconnect exposed the bridge port. Read VALIDATION_V2.md before powered
acceptance. Do not treat re-uploading as proof that cold startup is reliable.

## Protocol and evidence

Version 2 is intentionally incompatible with old SI-speed v1. Rebuild every ROS
participant together. USB uses strictly bounded JSON lines at 115200, receiver
challenges, session/boot generations, increasing command IDs, explicit operations,
and an independent 250 ms MCU drive watchdog. See the contract for exact schemas,
350 ms feedback deadlines, fault policy and command-application correlation.

OC records whole-run JSONL under `run_logs/`. Save logs, source/build hashes and
physical timing observations together. Software, board communication and physical
motion are separate acceptance results. Never replay a ROS drive bag into hardware.

Deferred: encoder rewiring, calibrated signed speed, closed-loop control, network
deployment, Xbox and peripheral restoration. Existing bridge/legacy packages remain
for historical regressions; they are not the active motor-control route.
