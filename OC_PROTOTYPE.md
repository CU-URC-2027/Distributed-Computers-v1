# Local C2 → OC → simulated Arduino (v2)

This is the current operating guide for the local ROS 2 command-authority prototype.
It uses **OPEN_LOOP_PWM**, normalized throttle/steering, and protocol version 2.
For the complete contract, see [INTERFACE_V2.md](INTERFACE_V2.md); for recorded
validation and hardware limitations, see [VALIDATION_V2.md](VALIDATION_V2.md).

## Start the local simulation

If the simulation is already running, use it rather than starting another OC in
the same namespace and domain. Otherwise, in Terminal 1:

```bash
cd ~/weeny_ws
./scripts/build.sh        # needed only after source/interface changes
export ROS_DOMAIN_ID=92
./scripts/run_demo.sh
```

This starts C2 GUI, OC and a separate simulated Arduino process. The default ROS
namespace is `/rover1`. The simulated serial link is a Linux PTY at
`/tmp/mini-rover-arduino`; no physical Arduino is needed. Simulation provenance
is displayed in C2 and included in chassis feedback. A PTY does not model physical
USB timing, electrical PWM, encoder calibration, or mechanical motion.

In **each additional terminal**, prepare the same environment:

```bash
source /opt/ros/jazzy/setup.bash
source ~/weeny_ws/install/setup.bash
export ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST
export ROS_DOMAIN_ID=92
```

The demo launcher defaults to domain 92, but a separately opened terminal must
set its domain too. `use_sim_time` is not used; leases use local monotonic clocks.
Stop a foreground launch with Ctrl+C. Closing the GUI ends its operator input;
the OC/simulator launch can remain running.

## GUI controls

1. Click **Connect / claim OC**. Wait for fresh OC/MCU state and acknowledgment
   of a new neutral command after this claim.
2. Set both fields to `0.0`, release the hold button, and click **Enable at neutral**.
3. Wait for OC authorization and MCU-confirmed enable.
4. Enter throttle and steering, then press and hold **HOLD TO DRIVE**.
5. Release to request zero while armed. Leaving the hold control also releases it;
   losing window focus disarms. Entered values are not a latched motion command.
6. **Disarm** disables. **STOP** latches disabled. **Reset STOP** clears the latch
   while remaining disabled; a fresh deliberate enable is required to drive again.

Throttle and steering are finite fractions in `[-1, 1]`: positive throttle means
forward; positive steering means left/CCW. They are not m/s or rad/s. The default
PWM ceiling is 63 out of 255. OC mixes left = throttle − steering and
right = throttle + steering, scales both together to preserve their ratio when
needed, and rounds to signed integer PWM. FL/RL share left; FR/RR share right.

The dashboard distinguishes selected input, OC-requested/limited PWM and
MCU-applied PWM. Four raw pulse counters have individual validity and direction
validity. Unavailable or stale measurements display N/A; this mode has no calibrated
speed/error display. MCU-applied means its software updated the output state.

## Headless simulation CLI

The CLI is **simulation-only**. It requires `--simulation`, normalized
`--throttle` and `--steering`, and fresh feedback labelled simulated. Physical
GUI driving remains a separate workflow.

For a new headless session, Terminal 1:

```bash
cd ~/weeny_ws
export ROS_DOMAIN_ID=92
./scripts/run_demo.sh gui:=false
```

Terminal 2 (or reuse an existing prepared terminal):

```bash
source /opt/ros/jazzy/setup.bash
source ~/weeny_ws/install/setup.bash
export ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST
export ROS_DOMAIN_ID=92
ros2 run mini_rover_c2 c2_cli --simulation --throttle 0.20 --steering -0.25 --duration 5
```

This claims authority, enables at neutral, holds the normalized test input for
five seconds, prints control-state JSON, then disarms. Negative steering requests
right/clockwise. The previous velocity-based CLI options are no longer supported.
Use `ros2 run mini_rover_c2 c2_cli --help` to inspect installed options.

If a GUI already owns control, the CLI cannot take over. Close that C2 client or
check **Pause command publication (watchdog test)**, then allow its 250 ms input
lease to expire before starting the CLI. Disarming alone does not release ownership
while the GUI continues publishing fresh neutral input. After the CLI exits and
its lease expires, reconnect/claim from the GUI and enable again at neutral.

For a custom namespace, launch with `namespace:=another_rover` and pass
`--namespace /another_rover` to separately started clients. All participants must
also share the same ROS domain.

## Validate command authority

- Only one C2 session/source owns control. A second client's claim and enable
  request are rejected while the current owner remains valid.
- Enable requires newly acknowledged neutral input with hold released. C2 process
  identity and increasing command IDs persist across reclaims; an acknowledgment
  from before a timeout cannot satisfy a new claim's readiness check.
- OC issues short-lived challenges and an authorization generation. Valid newer
  input must echo current authority; old/duplicate/out-of-order input does not
  renew the lease. These fields are freshness controls, not authentication.
- C2 input expires after 250 ms. OC withdraws authority and stops forwarding drive.
  The simulated MCU separately expires its drive lease after 250 ms. Polls, PING,
  malformed packets and duplicate commands do not renew drive permission.
- MCU feedback and C2 state have separate 350 ms freshness thresholds. Unknown or
  stale MCU enable is shown explicitly rather than inferred from the last value.
- STOP/reset, restarts, timeouts and reconnects never automatically re-enable.
  Enable/reset acceptance by an OC service is distinct from MCU confirmation.

A simple GUI timeout test: enable, hold a nonzero simulated command, then check
**Pause command publication (watchdog test)**. Authority must expire and confirmed
PWM return to zero. Uncheck pause, return fields to zero, claim again and deliberately
enable. First-fault evidence remains visible after recovery.

## ROS interfaces

Only two application ROS nodes implement the control route:

| Node | Role |
|---|---|
| `/rover1/c2_operator` | Operator input publication, authority service client, feedback display |
| `/rover1/oc` | Ownership, validation, limiting, freshness supervision, serial adapter |

The simulated Arduino is a separate process connected through a PTY, not a ROS node.

| Interface under `/rover1` | Direction / purpose |
|---|---|
| `c2/drive_intent` | C2 → OC; v2 normalized intent, process/command identity, challenge and generation |
| `control/claim` | C2 → OC service; exclusive input ownership |
| `control/set_enabled` | C2 → OC service; explicit enable/disarm request |
| `control/stop`, `control/reset_stop` | C2 → OC services; STOP latch and disabled reset |
| `control/state` | OC → C2; authority, confirmed enable/validity, identities, limits, link ages and faults |
| `chassis/state` | OC → C2; MCU-confirmed side PWM and named raw encoder samples/validity |
| `diagnostics` | OC → observers; link, freshness and logging health |

Topic QoS is best effort, volatile, depth 1, lifespan 250 ms. OC state publishes at
20 Hz; chassis feedback is published for new MCU samples, approximately 10 Hz plus
operation responses. Exact fields are defined in `mini_rover_interfaces` and the
shared contract. The v2 serial link uses bounded JSON lines, two signed side PWM
targets, boot/session generations, transport/application IDs and 200 ms MCU-issued
sample tokens; it is intentionally incompatible with the old wheel-speed v1 schema.

## Observe the running system

Run the environment setup above in each observation terminal, then use:

```bash
ros2 node list --no-daemon
ros2 node info /rover1/c2_operator
ros2 node info /rover1/oc
ros2 topic echo /rover1/control/state --qos-reliability best_effort
ros2 topic echo /rover1/chassis/state --qos-reliability best_effort
ros2 topic hz /rover1/chassis/state
ros2 service list
ros2 run rqt_graph rqt_graph
```

In rqt_graph, choose **Nodes/Topics (all)** and use `/rover1/.*` for both node and
topic filters. `/rover1` alone is an exact-name filter, so it can hide everything.
Refresh after discovery. Standard rqt_graph draws topic connections; use service
listing/node info for the claim/enable/STOP/reset service connections.

OC writes whole-run JSONL under `~/weeny_ws/run_logs/`, including configuration,
accepted/rejected inputs, operations, MCU feedback and state. Receipt clocks and
source identities are preserved. Simulation model counts remain explicitly synthetic.

## Simulator fault injection

Default fault file: `/tmp/mini-rover-faults.json`. If the launch uses a custom
`fault_file:=...`, modify that actual file instead. Each write replaces the active
fault selection; use `{}` to clear it.

```bash
printf '%s\n' '{"missing":["rear_right"]}' > /tmp/mini-rover-faults.json
printf '%s\n' '{"silence":true}' > /tmp/mini-rover-faults.json
printf '%s\n' '{"drop_commands":true}' > /tmp/mini-rover-faults.json
printf '%s\n' '{"delay_ms":400}' > /tmp/mini-rover-faults.json
printf '%s\n' '{}' > /tmp/mini-rover-faults.json
```

Missing encoders are visible **warnings** in OPEN_LOOP_PWM and do not inhibit drive.
The optional `stalled` wheel list changes synthetic pulse generation; it is not a
physical stall detector. Missing MCU acknowledgments, expired inputs and stale
feedback inhibit motion. Clear injected transport faults and deliberately reclaim/
re-enable; recovered telemetry never resumes motion automatically.

## Verification and physical scope

```bash
cd ~/weeny_ws
./scripts/run_tests.sh
```

The test runner uses a separate local ROS domain (97 by default), real ROS/PTY
integration, pure controllers, GUI regressions and native firmware parity checks.
See VALIDATION_V2.md for recorded results. The actual firmware has been built and
uploaded, and unpowered board acknowledgments have been checked; physical motor
movement, electrical waveforms and calibrated speed remain unverified. The current
user workflow is local simulation. Hardware boot/reconnect observations are recorded
in that validation document and do not affect this simulated CLI workflow.
