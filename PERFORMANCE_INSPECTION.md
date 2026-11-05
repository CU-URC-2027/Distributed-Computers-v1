# Performance inspection on separate computers

Printable instructions: [PDF run guide](output/pdf/performance-inspection-run-guide.pdf).

Optional companion processes sample the local C2/OC process and computer, observe
ROS traffic, and write rotating JSONL logs. A separate desktop dashboard receives
both computers' samples. Monitoring never claims authority, sends drive intents,
or opens the motor serial port. All timing observations are made in the monitor
process; they are not measurements of the control node's callback execution time.

## Install on both computers

Use the same updated source on both machines. From `~/weeny_ws`:

```bash
source /opt/ros/jazzy/setup.bash
rosdep install --from-paths src --ignore-src -r -y
./scripts/build.sh
source install/setup.bash
```

The new package is `mini_rover_inspection`; its additional Python dependencies are
`python3-psutil` and `python3-tk`. Use your working distributed ROS environment in
each terminal, including the same domain ID, namespace, discovery configuration,
and middleware as your existing two-computer deployment. The new launchers inherit
those settings. They do not impose `LOCALHOST` or change static peers. The old
`run_demo.sh`, `run_hardware.sh`, and `dcv1` tools retain their existing local behavior.

## Launch with inspection

On the **C2 computer**, with a graphical desktop:

```bash
ros2 launch mini_rover_c2 c2.launch.py namespace:=rover1 \
  inspect_performance:=true inspection_dashboard:=true
```

On the **OC computer**, use the stable port of your verified v2 board:

```bash
ros2 launch mini_rover_oc oc.launch.py namespace:=rover1 \
  port:=/dev/serial/by-id/YOUR_VERIFIED_BOARD \
  inspect_performance:=true
```

The OC launcher starts only OC and its optional observer. Hardware remains the
default (`simulated:=false`). To observe an already running simulator instead,
pass `simulated:=true port:=/path/to/its/pty-link`; the link must resolve to an
existing PTY. Each launcher attaches the monitor to its exact node PID, without
guessing from process names. A node exit ends that monitor. A monitor or dashboard
failure does not shut down the control node. Closing the C2 GUI ends its launch
session and companions. Ctrl+C in either launch terminal cleans up its processes.

Set `inspect_performance:=false` (the default) to start only the existing node.
`inspection_dashboard:=true` only takes effect when inspection is enabled.

| Setting | Default | Meaning |
|---|---|---|
| `inspect_performance` | `false` | Start the local companion monitor |
| `inspection_dashboard` | `false` | Open the desktop viewer on this computer |
| `inspect_interval` | `1.0` | Resource sampling/reporting interval in seconds, 0.2–60 |
| `inspect_log_directory` | `~/weeny_ws/performance_logs` | Local performance JSONL directory |
| `namespace` | `rover1` | Shared namespace of the monitored rover |

ROS launch uses `name:=value` syntax. The standalone monitor is also available if
you want to keep your existing node startup commands. Run it on the **same machine
as the process**, providing that process's PID:

```bash
ros2 run mini_rover_inspection performance_monitor \
  --role oc --pid 12345 --namespace /rover1 --interval 1 \
  --log-directory ~/weeny_ws/performance_logs
```

Use `--role c2` on the operator computer. The monitor verifies process identity
including creation time and stops when its target exits. It does not reattach to
a recycled PID. Restart it after restarting a manually launched node.

Open the viewer independently on the C2 computer or another computer with the
same ROS configuration and a graphical desktop:

```bash
ros2 run mini_rover_inspection performance_dashboard --namespace /rover1
```

## Read the dashboard

The Overview shows each host and PID, CPU and RAM trends, peak reported input and
feedback ages, controller status, and monitor overhead. Select a source if several
C2 or OC instances are present. The All measurements tab includes NIC throughput,
interface errors/drops, disk throughput, swapping, temperatures/frequency, process
threads/file descriptors/context switches, topic timing, and local logging status.
Faults & events retains transitions captured between samples, including faults
that cleared before the next one-second resource report.

The viewer holds at most 600 samples per source and 16 sources. Charts show
up to the last five minutes per source (two minutes at 0.2-second sampling);
remote clocks are not assumed synchronized. A live
source becomes **STALE** after no samples for the larger of three seconds or three
sample intervals. Old readings stay labelled as last known; an absent host is not
zero load. **EXITED** means the monitor reported target termination. A recorded
session is labelled **RECORDED** and never presented as live.

Measurements and their limits:

- Process CPU uses 100% for one fully occupied logical core and can exceed 100%.
  Host CPU is averaged across cores; the busiest core is shown separately. Process
  RAM is resident memory; it is not the sum of all descendant processes.
- Topic rates, maximum gaps, and 95th percentile gaps use the monitor's own callback
  receipt times. The observer can be delayed independently of the control node.
  Gaps and short fault transitions are captured continuously and aggregated once
  per resource sample. The first rate sample includes discovery/startup time.
- Input/feedback ages come from OC's monotonic clock. Negative/never-seen ages are
  N/A. Input peaks are omitted while no C2 session exists, since unclaimed C2
  legitimately publishes no intents. The 250 ms input and 350 ms feedback lines
  are the current v2 contract limits, not measured physical stopping times.
- Receipt age is time since that monitor saw a message. OC-reported ages and
  receipt ages are separate; neither measures one-way network delay. No remote
  wall clock is subtracted from a local monotonic clock. Wall timestamps in JSONL
  support correlation with OC logs, subject to clock synchronization error.
- Sequence gaps are **unobserved values**, not a network packet-loss measurement.
  OC can intentionally publish only the latest of several MCU samples. Duplicate
  or reversed sequences are counted; a new boot/session resets the sequence baseline.
- NIC rates and errors are host-wide counters, including other applications.
  They do not isolate ROS traffic or measure end-to-end DDS loss/latency.
- Temperatures and CPU frequency are shown where the OS exposes them; missing
  sensors remain N/A. These slower reads run in a background thread every five
  seconds; their age appears in All measurements. Frequency changes alone do not prove thermal throttling.
  Power consumption, MCU CPU/RAM, internal callback durations, exact service RTT,
  malformed serial-frame totals, and physical USB byte counts are not measured.
- A serial port marked open is distinct from fresh MCU feedback. The diagnostic
  serial error is the most recent reported text, with its receipt age available.
  Simulated versus hardware provenance comes from chassis telemetry.

## Recording and replay

Each monitor writes to its own machine under `~/weeny_ws/performance_logs/`, with
a unique role/timestamp/run identifier. A file rotates at 20 MiB, retaining four
backups (about 100 MiB per monitor run). Old runs are not automatically deleted;
archive or remove them deliberately when no longer needed. An asynchronous bounded
writer reports errors and dropped records in the metrics. Inspection logging
failure does not alter motor authority. OC's own control log behavior is unchanged.

`/<namespace>/inspection/metrics` uses versioned JSON in `std_msgs/String` at the
configured interval, with best-effort delivery and bounded queues. The JSONL has
the same schema. Both include hostname, host boot identity, monitor identity,
target PID/creation time, namespace/domain, sample sequence, and wall/monotonic
timestamps. A missing remote stream leaves the locally recorded samples intact.

Copy the OC logs to the viewing computer using your normal file transfer method,
then open either or both roles' files:

```bash
ros2 run mini_rover_inspection performance_dashboard --replay \
  /path/to/c2-run.jsonl /path/to/oc-run.jsonl
```

For rotated parts, list older files first (`.4`, `.3`, `.2`, `.1`, then `.jsonl`).
Replay displays a bounded recent history; it does not connect to ROS or drive a
node. Invalid/truncated lines are skipped and counted in the viewer header.

## Verify a deployment

Start the nodes disabled, confirm both hostnames/PIDs appear, and compare process
CPU/RAM with your OS process monitor. Check that both performance JSONL files grow
locally. Stop one inspection process and confirm its card becomes stale while
the control node continues. During a controlled simulator run, disconnect the
network and inspect the local logs afterward; then repeat under your established
hardware test procedure if needed. Clock synchronization is needed for precise
cross-machine wall-time correlation, not for the local age/gap measurements.

The implementation is tested with local ROS/DDS, harmless target processes, and
the PTY simulator. It still needs a deployment check on your two physical machines;
local tests cannot establish their network latency or overhead.

### Verification recorded November 5, 2026

- The new package and both deployment packages built successfully with colcon.
- The workspace regression command passed **239 Python tests**, the standalone
  C++ guard checks, and **131 native/Python protocol conformance checks**.
- After moving slow sensor reads to a separate thread, the final focused check
  passed **57 tests** covering measurement semantics, recording/rotation/failure,
  real DDS diagnostics, transient faults, PID lifetime, launch argument behavior,
  Tk rendering/replay, malformed inputs, and the full simulator launch path.
- The launch test started the real C2 GUI and OC against a unique simulator PTY,
  kept control disabled, verified both streams and local logs, terminated OC's
  monitor while OC continued publishing, and confirmed that disabled inspection
  created neither companions nor performance logs.
- The final short local run at 0.5-second sampling measured mean main sampling
  callback duration of approximately 4.5 ms (OC observer) and 5.5 ms (C2 observer).
  These are observations of this laptop under test, not deployment guarantees.

Local evidence, excluded from Git, is under `validation/performance-inspection/`:
`regression-tests.log`, `inspection-tests.log`, `final-check/`, and
`dashboard-recorded.png`. The screenshot contains actual local simulator samples
and is explicitly labelled as a recorded session. No physical motor or remote
computer test was performed for this change.
