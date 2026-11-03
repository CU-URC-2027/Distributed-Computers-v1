# Parameters and test scenarios

Edit one YAML file to repeat local command-authority experiments with controlled inputs. The package validates settings before launch, keeps protocol timing fixed, and records the resolved launch settings with each run. These tools use the existing workspace and a PTY simulator; they do not exercise physical motors. Prepared November 3, 2026.

## Configuration and precedence

The default file is `config/settings.yaml`. Pass `--config /absolute/file.yaml` to select another file. CLI flags override YAML for that invocation only. Unknown settings and duplicate YAML keys are rejected rather than silently accepted. Values must have the expected YAML types; booleans are not numeric command inputs.

| Setting | Default | Valid values and effect |
|---|---|---|
| workspace | `~/weeny_ws` | Path to the existing built v2 workspace |
| domain | 92 | Integer 0–232; same value for cooperating ROS participants |
| namespace | `/rover1` | Absolute non-root ROS namespace with valid name segments |
| gui | true | Boolean; GUI start requires a desktop display |
| command.throttle | 0.20 | Finite −1 to 1; positive is forward |
| command.steering | −0.25 | Finite −1 to 1; positive is left/CCW |
| command.duration | 5.0 | Finite seconds greater than 0, at most 3600 |
| oc.pwm_limit | 63 | Integer 1–63; OC side-output ceiling |
| simulator.response_time | 0.20 | Finite positive seconds; simulator response model |
| fault.delay_ms | 0 | Finite 0–2000 ms on serial feedback path |
| fault.missing | `[]` | Distinct named wheel channels from the list below |
| fault.drop_commands | false | Boolean; deterministic simulated command-drop switch |
| fault.silence | false | Boolean; deterministic simulated feedback-silence switch |

Channel aliases in the diagrams are FL, RL, FR and RR. YAML and `--missing` use `front_left`, `rear_left`, `front_right`, `rear_right`. A comma-separated CLI list has no spaces, for example `--missing front_left,rear_right`; `--missing ''` selects none.

Workspace/domain/namespace/GUI, ceiling and response time are launch settings: stop and restart to apply edits. Startup verifies a healthy disabled simulated handshake before applying the configured fault baseline; it does not enable. Each `command` reads command settings anew. Each `fault` reads the YAML baseline and that invocation's flags, then replaces the active fault file. A new fault call does not inherit flags from the previous call. No-argument `fault` applies YAML; `fault --clear` always writes healthy defaults even if YAML contains a fault.

Protocol v2, OPEN_LOOP_PWM mode, watchdogs, challenge/sample-token lifetimes and publication rates are fixed. OC ceiling may be reduced but the simulator's MCU hard ceiling stays 63. Do not edit unrelated workspace configuration to bypass this package's limits.

## Command examples

Run from the package folder. Package commands source ROS and the configured workspace automatically. For direct ROS commands use the environment setup in the README in every terminal.

```bash
./scripts/dcv1 start --headless --pwm-limit 31 --response-time 0.5
./scripts/dcv1 command --throttle 0.6 --steering 0.2 --duration 2
./scripts/dcv1 fault --missing rear_right
./scripts/dcv1 fault --clear
./scripts/dcv1 stop
```

If domain or namespace overrides were used at start, repeat them on later commands or store them in YAML. Changing a YAML launch setting while the old session runs does not reconfigure that process. The command result uses the active session's recorded ceiling, so a later file edit cannot silently reinterpret its applied PWM.

The OC first computes throttle minus steering for left and throttle plus steering for right. It ratio-scales values whose magnitude exceeds one, multiplies by the configured ceiling, and rounds symmetrically to signed integers. At ceiling 63, `(0.20, −0.25)` gives `(28, −3)`; `(1, 1)` gives `(0, 63)`. FL/RL share left and FR/RR share right. PWM fractions are duty-cycle demands, not calibrated velocities.

A longer model response time changes synthetic response and pulse generation. It does not change authorization deadlines. `delay_ms` instead delays outgoing simulated serial feedback. Neither parameter is a DDS network emulator.

## Expected observations

| Scenario | Action and pass observation |
|---|---|
| Healthy command | Headless `command` with changed throttle/steering; fresh simulated applied PWM matches mixed target and correlated identity |
| PWM limiting | Restart at ceiling 31; outputs remain within ±31 while simulator hard cap stays 63 |
| Response time | Restart with response time 0.5; launch record contains the changed value and model response changes, without changing watchdogs |
| Competing owner | Claim from GUI, then run command; competing client is refused without takeover |
| Hold release | Hold a GUI command, release; applied PWM reaches zero while armed state may remain enabled |
| STOP and reset | STOP latches disabled; Reset STOP clears latch but leaves disabled until a deliberate neutral enable |
| Publication timeout | Pause GUI publication while holding; OC authority expires after the 250 ms input lease |
| Delayed feedback | Apply 400 ms delay while enabled; feedback exceeds freshness limit and permission is withdrawn |
| Dropped commands | Set drop_commands true; valid drive renewal ceases and MCU drive watchdog disables |
| Silent feedback | Set silence true; stale MCU feedback becomes unknown and inhibits drive |
| Missing encoder | Mark rear_right missing; that channel is invalid and a warning appears without blocking open-loop drive |
| Recovery | Clear fault; fresh feedback returns but control remains disabled |
| Conflict and cleanup | Duplicate start is refused; stop removes only matching package-owned processes and leaves unrelated work alive |

The scenarios describe acceptance observations, not a statement that every manual GUI interaction has already been performed. The evidence folder and validation guide identify the checks actually executed. Software deadline checks do not measure electrical shutdown or physical stopping time.

## Repeatable automated authority check

```bash
./scripts/dcv1 test
./scripts/dcv1 test --output /absolute/path/to/new-empty-directory
```

Tests default to domain 97, GUI off and a unique namespace, leaving the normal domain 92 session separate. They create their own PTYs/logs and clean up only their own recorded processes. A configured domain may be overridden explicitly; avoid choosing the operational domain for experiments. Output directories must be empty to preserve previous evidence.

Inspect `result.json`: `passed` must be true, each expected check must be present, and cleanup must show no remaining owned process. Failure information and process/client/OC logs explain the first unmet observation. A partially completed run is not an overall pass. The command tester requires fresh simulated feedback and reports competing ownership clearly; it will not run physical driving.

The suite checks service rejection and confirmed application independently. A service accepting an enable request does not prove the MCU enabled. A sent target does not prove it was applied. Follow the returned source session, generation, command/transport identities, and fresh MCU-applied sample when assessing the result.

## Manual fault runs

Start healthy and headless for a CLI run, or use GUI hold control. A fault can make a command exit with a refusal or authority-loss error; this is expected when the experiment intentionally removes valid permission.

```bash
./scripts/dcv1 fault --delay-ms 400
./scripts/dcv1 fault --drop-commands
./scripts/dcv1 fault --silence
./scripts/dcv1 fault --missing rear_right
./scripts/dcv1 fault --clear
```

With a healthy YAML baseline, these examples each replace the previous fault. With a nonhealthy YAML baseline, its other fault settings remain active until overridden in that call or cleared with `--clear`. Inspect the command's returned resolved fault object and the session's `faults.json` to know exactly what is active.

Serial faults affect OC↔simulator. To test C2→OC expiration, use the GUI **Pause command publication (watchdog test)** option. Disarm alone leaves a publishing GUI as owner. To hand ownership to a CLI, close that C2 client or pause its publication and allow the input lease to expire. Return fields to zero, release hold, reclaim and enable deliberately after recovery.

## Read logs without overstating evidence

`launch_spec.json` records effective process arguments and settings. `faults.json` records active simulator fault selection. Simulator/OC/GUI process logs expose startup failures. OC JSONL and command-client JSONL preserve received state and identities. Command result files record the request, active ceiling and confirmed application. Cleanup records identify signaled processes.

Raw encoder counts are per-channel measurements with validity flags and MCU sample identity/time. Missing channels must show invalid/N/A rather than a plausible zero speed. Fresh OC publication alone cannot refresh an old sensor sample. Simulated motion/counts remain labelled synthetic. First-fault retention helps explain why a later healthy-looking state is still disabled.

Bad input should fail before new processes are launched: try a throttle outside ±1, zero duration, nonfinite response time, ceiling 64, unknown YAML key, duplicate key, or unrecognized channel. Restore a valid file before starting the next experiment. Never raise the ceiling beyond 63 to compensate for absent physical movement.
