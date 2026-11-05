#!/usr/bin/env python3
"""Build the printable run guide. Requires reportlab; does not start ROS nodes."""
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                               TableStyle, Preformatted, PageBreak, Image)

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT/'output/pdf/performance-inspection-run-guide.pdf'
INK, BLUE, MUTED = colors.HexColor('#182b3c'), colors.HexColor('#087b86'), colors.HexColor('#526473')
LIGHT, BORDER = colors.HexColor('#f0f5f7'), colors.HexColor('#d7e1e7')
FONT_DIR = Path('/usr/share/fonts/truetype/dejavu')
for name, file in [('Body', 'DejaVuSans.ttf'), ('Bold', 'DejaVuSans-Bold.ttf'), ('Code', 'DejaVuSansMono.ttf')]:
    pdfmetrics.registerFont(TTFont(name, str(FONT_DIR/file)))
pdfmetrics.registerFontFamily('Body', normal='Body', bold='Bold', italic='Body', boldItalic='Bold')
styles = {
    'body': ParagraphStyle('body', fontName='Body', fontSize=9.5, leading=14, textColor=INK, spaceAfter=8),
    'title': ParagraphStyle('title', fontName='Bold', fontSize=24, leading=29, textColor=INK, spaceAfter=12),
    'h2': ParagraphStyle('h2', fontName='Bold', fontSize=12, leading=16, textColor=BLUE, spaceBefore=10, spaceAfter=7),
    'small': ParagraphStyle('small', fontName='Body', fontSize=8, leading=11, textColor=MUTED, spaceAfter=7),
    'code': ParagraphStyle('code', fontName='Code', fontSize=8, leading=11, textColor=INK),
}
story = []


def p(text, style='body'):
    story.append(Paragraph(text, styles[style]))


def code(text):
    lines = text.strip('\n').splitlines()
    for line in lines:
        if pdfmetrics.stringWidth(line, 'Code', 8) > 480:
            raise ValueError('Code line too long: '+line)
    box = Table([[Preformatted('\n'.join(lines), styles['code'])]], colWidths=[504])
    box.setStyle(TableStyle([('BACKGROUND', (0,0), (-1,-1), LIGHT),
                            ('BOX', (0,0), (-1,-1), .5, BORDER),
                            ('LEFTPADDING', (0,0), (-1,-1), 12),
                            ('RIGHTPADDING', (0,0), (-1,-1), 12),
                            ('TOPPADDING', (0,0), (-1,-1), 10),
                            ('BOTTOMPADDING', (0,0), (-1,-1), 10)]))
    story.extend([box, Spacer(1, 9)])


def table(rows, widths):
    data = [[Paragraph(escape(str(cell)), styles['small']) for cell in row] for row in rows]
    t = Table(data, colWidths=widths, hAlign='LEFT')
    t.setStyle(TableStyle([('BACKGROUND', (0,0), (-1,0), LIGHT),
                          ('VALIGN', (0,0), (-1,-1), 'TOP'),
                          ('LINEBELOW', (0,0), (-1,-1), .4, BORDER),
                          ('LEFTPADDING', (0,0), (-1,-1), 8),
                          ('RIGHTPADDING', (0,0), (-1,-1), 8),
                          ('TOPPADDING', (0,0), (-1,-1), 7),
                          ('BOTTOMPADDING', (0,0), (-1,-1), 5)]))
    story.extend([t, Spacer(1, 8)])


def page(number, title):
    if story:
        story.append(PageBreak())
    p(f'RUN GUIDE  /  {number:02d}', 'small')
    p(title, 'title')


page(1, 'Performance inspection')
p('C2 and OC on separate computers', 'h2')
p('Run an optional monitor beside each node, view both computers in one desktop dashboard, '
  'and keep local performance logs for later review. This guide assumes Ubuntu with ROS 2 Jazzy '
  'and your existing, working C2-to-OC network configuration.')
table([['C2 computer', 'Shared ROS network', 'OC computer'],
       ['C2 GUI + local monitor\nPerformance dashboard',
        'Same domain and rover namespace\nPerformance topic: /rover1/inspection/metrics',
        'OC + local monitor\nUSB board or PTY simulator']], [168, 168, 168])
p('1. Update and build on BOTH computers', 'h2')
p('For an existing checkout, stop its nodes before rebuilding. Preserve any local edits before pulling; '
  'do not discard them if Git reports a conflict.')
code('''cd ~/weeny_ws
git pull --ff-only origin main
source /opt/ros/jazzy/setup.bash
rosdep install --from-paths src --ignore-src -r -y
./scripts/build.sh
source install/setup.bash''')
p('For a new computer, clone first, then run the build commands above:', 'small')
code('''git clone \\
  https://github.com/CU-URC-2027/Distributed-Computers-v1.git \\
  ~/weeny_ws''')
p('2. Prepare EVERY terminal used below', 'h2')
code('''source /opt/ros/jazzy/setup.bash
source ~/weeny_ws/install/setup.bash''')
p('Apply the same ROS_DOMAIN_ID, discovery/static-peer settings, and middleware that already work '
  'between your computers. Use the same namespace on both; examples use rover1. The new launchers '
  'inherit your settings. The older run_demo.sh, run_hardware.sh, and dcv1 tools still impose their '
  'existing local configuration.')
p('Additional dependencies: python3-psutil and python3-tk. The dashboard and C2 GUI need a graphical desktop. '
  'Prepared November 5, 2026. Instructions: pages 2-3; dashboard: page 4; logs/settings: page 5; troubleshooting: page 6.', 'small')

page(2, 'Run with the hardware OC')
p('Use this page for the OC connected to your verified v2 Arduino. For a simulator, follow page 3 instead. '
  'Start only one OC for a given serial port and rover namespace.')
p('1. On the OC computer', 'h2')
p('Find the stable USB device path, then replace YOUR_VERIFIED_BOARD with the correct entry. '
  'Use your established board and motor commissioning procedure.')
code('''ls -l /dev/serial/by-id/

ros2 launch mini_rover_oc oc.launch.py namespace:=rover1 \\
  port:=/dev/serial/by-id/YOUR_VERIFIED_BOARD \\
  inspect_performance:=true''')
p('This starts OC and its local monitor. Hardware mode is the default. It does not start a C2 GUI '
  'on the OC computer. The monitor records locally even if the network connection drops.')
p('2. On the C2 computer', 'h2')
code('''ros2 launch mini_rover_c2 c2.launch.py namespace:=rover1 \\
  inspect_performance:=true inspection_dashboard:=true''')
p('Two windows open: the existing C2 control GUI and the performance dashboard. The dashboard should '
  'show C2 and OC, with their actual hostnames and process IDs. Allow a few seconds for ROS discovery.')
p('3. Confirm observation before operating', 'h2')
p('Keep the controller disabled while checking the display. Confirm both cards show <b>LIVE</b>, '
  'the OC reports the expected hardware provenance, and the local log files are growing on both computers. '
  'C2 intent traffic can be absent before you claim control; that is normal.')
p('Continue using the existing C2 Connect/claim, neutral Enable, and hold-to-drive controls when ready. '
  'The performance dashboard only observes the system.')
p('Stop or turn inspection off', 'h2')
p('Press Ctrl+C in each launch terminal to stop its processes. Closing the C2 control GUI ends its '
  'launch session and companions. Closing only the performance window leaves control running. '
  'A monitor failure also leaves the control node running.')
code('''# Start C2 without performance inspection:
ros2 launch mini_rover_c2 c2.launch.py \\
  inspect_performance:=false''')
p('For OC, keep the required port argument and set inspect_performance:=false. Inspection is off '
  'by default. ROS launch uses name:=value syntax, rather than --inspect-performance.', 'small')

page(3, 'Simulation or existing node sessions')
p('The simulation route uses the same two-computer network and dashboard, with a PTY simulator '
  'running on the OC computer. No physical Arduino is required.')
p('Simulation: OC computer, terminal A', 'h2')
code('''ros2 run mini_rover_sim arduino_sim \\
  --port-link /tmp/rover-inspection-arduino''')
p('Leave this terminal running. The simulator prints its PTY path. If this path is already active, '
  'use the existing simulator or choose a new path consistently; do not start a duplicate.')
p('Simulation: OC computer, terminal B', 'h2')
code('''ros2 launch mini_rover_oc oc.launch.py namespace:=rover1 \\
  port:=/tmp/rover-inspection-arduino simulated:=true \\
  inspect_performance:=true''')
p('On the C2 computer, use the C2 command from page 2. The dashboard will label chassis data '
  '<b>SIMULATION</b>. The simulator must be running before launching OC. Stop OC and C2 with Ctrl+C, '
  'then stop the simulator in terminal A.')
p('Attach to an already running node', 'h2')
p('Keep your current startup commands and run a standalone monitor on each computer. Use the actual '
  'local c2_gui or oc process ID from your process manager, not the ros2 launch wrapper PID. '
  'The number 12345 below is an example to replace.')
code('''ros2 run mini_rover_inspection performance_monitor \\
  --role oc --pid 12345 --namespace /rover1 --interval 1 \\
  --log-directory ~/weeny_ws/performance_logs''')
p('On the C2 computer, use --role c2 and its local C2 process ID. Each monitor tracks that process '
  'and exits when it exits. Restart a manually attached monitor after restarting its node.')
p('Open the dashboard independently', 'h2')
code('''ros2 run mini_rover_inspection performance_dashboard \\
  --namespace /rover1''')
p('Run the viewer on the C2 computer, or another computer with the same ROS environment and a '
  'graphical desktop. Standalone monitors stop with Ctrl+C without stopping the node they observe.', 'small')

page(4, 'Read the dashboard')
screenshot = ROOT/'docs/images/performance-inspection.png'
if screenshot.exists():
    story.append(Image(str(screenshot), width=504, height=315))
    story.append(Spacer(1, 7))
p('Example: actual local simulator samples, explicitly shown as a recorded session. Both roles '
  'ran on one laptop for this verification; your deployment should display its two hostnames.', 'small')
table([['Area', 'What it tells you'],
       ['Overview', 'CPU/RAM trends; busiest core; free memory/disk; temperature; OC input and MCU feedback ages; controller state; monitor overhead.'],
       ['All measurements', 'NIC and disk throughput, interface errors/drops, swap, sensor age, process threads and file descriptors, topic rates/gaps, serial diagnostics, and logging status.'],
       ['Faults & events', 'Fault appearances/clearances, identity changes, serial errors, and process exits captured between resource samples.']], [112, 392])
p('<b>LIVE</b> means samples are arriving. <b>STALE</b> means none arrived for the larger of three '
  'seconds or three sample intervals; shown values are last known. <b>EXITED</b> means target termination '
  'was reported. <b>RECORDED</b> means you are viewing a saved session.')
p('Process CPU uses 100% for one fully occupied core and can exceed 100%. Host CPU averages all cores. '
  'RAM is the process resident memory. N/A means unavailable, not zero. Select a source at the top of '
  'a card if multiple sessions exist.')
p('Charts show up to five minutes per source, limited to 600 retained samples (two minutes at 0.2-second '
  'sampling). Input-age peaks are omitted when C2 has no control session.', 'small')

page(5, 'Settings, local logs, and replay')
table([['Launch setting', 'Default / purpose'],
       ['inspect_performance', 'false; enable the local companion monitor.'],
       ['inspection_dashboard', 'false; open the viewer here when inspection is also enabled.'],
       ['inspect_interval', '1.0 seconds; allowed range 0.2-60 seconds.'],
       ['inspect_log_directory', '~/weeny_ws/performance_logs; directory on this computer.'],
       ['namespace', 'rover1; must match between the participants.']], [190, 314])
p('Example: sample twice per second on C2', 'h2')
code('''ros2 launch mini_rover_c2 c2.launch.py \\
  inspect_performance:=true inspection_dashboard:=true \\
  inspect_interval:=0.5 \\
  inspect_log_directory:=/home/YOUR_USER/rover-perf''')
p('Replace YOUR_USER, or omit inspect_log_directory to use the default. Changes take effect when '
  'you restart that launch. Temperature and CPU-frequency sensors are read in a background thread '
  'every five seconds regardless of the resource interval.')
p('Find the recordings on EACH computer', 'h2')
code('''ls -lt ~/weeny_ws/performance_logs/''')
p('Every run creates a unique c2- or oc-prefixed JSONL file. Each file rotates at 20 MiB and retains '
  'four backup parts, about 100 MiB per monitor run. Old runs are not automatically deleted. '
  'Archive or remove them deliberately when no longer needed. Log errors and dropped records appear '
  'in the dashboard; a performance logging failure does not change motor authority.')
p('Open recorded data', 'h2')
p('Copy the OC files to the viewing computer using your normal file-transfer method. Replace these '
  'example paths with your actual files:')
code('''ros2 run mini_rover_inspection performance_dashboard --replay \\
  /path/to/c2-run.jsonl /path/to/oc-run.jsonl''')
p('For rotated parts, pass the oldest first: .jsonl.4, .jsonl.3, .jsonl.2, .jsonl.1, then .jsonl. '
  'Replay loads the bounded recent history, labels it RECORDED, and does not connect to ROS. '
  'Invalid or truncated lines are skipped and counted.')
p('Local logs keep samples through a network outage. Their wall timestamps can be correlated with '
  'the OC run logs, subject to the computers\' clock synchronization error.', 'small')

page(6, 'Checks and troubleshooting')
table([['Symptom', 'What to check'],
       ['Package or executable missing', 'Pull/build the updated source on that computer and source install/setup.bash in the terminal. Verify python3-psutil and python3-tk are installed.'],
       ['Only one host appears', 'Confirm inspection is enabled on both. Match namespace, ROS_DOMAIN_ID, discovery/static peers, middleware, and firewall settings with your working distributed setup.'],
       ['No graphical window', 'Open the dashboard in a graphical desktop session. OC monitoring can run headlessly. A plain SSH shell may not have a display.'],
       ['STALE card', 'Check the remote node and monitor processes, network connectivity, and remote local log. Stale performance data alone does not prove the control node failed.'],
       ['No C2 intent traffic', 'An unclaimed C2 session is normally silent. Check state/chassis traffic first; monitoring itself never claims control.'],
       ['OC simulation port rejected', 'Start the simulator first; simulated:=true requires an existing PTY or symlink to one. Hardware mode requires a /dev/serial/by-id/ path.'],
       ['Log error / missing sensors', 'Check disk space and directory permissions. Unsupported temperature or frequency sensors legitimately show N/A.']], [132, 372])
p('Read-only ROS checks, in a prepared terminal', 'h2')
code('''ros2 node list --no-daemon
ros2 topic info /rover1/inspection/metrics --verbose
ros2 topic hz /rover1/inspection/metrics''')
p('The topic combines all monitors: two monitors at a one-second interval normally contribute about '
  'two samples per second in total. It uses best-effort delivery; local JSONL is the source to inspect '
  'for samples absent from the remote viewer.', 'small')
p('What these measurements do and do not establish', 'h2')
p('The 250 ms input and 350 ms feedback lines reflect the current control contract. Ages come from '
  'OC or the observer\'s local clock; they are not one-way network delay or physical stopping time. '
  'Sequence gaps mean unobserved values, not proven packet loss. NIC counters include other '
  'applications. Temperature/frequency do not independently prove thermal throttling.')
p('Local verification passed 239 workspace Python tests plus C++ guard and 131 native/Python checks. '
  'The final sensor-thread change passed 57 focused tests, including real launchers, DDS diagnostics, '
  'recording, GUI rendering, and the PTY simulator. This feature still needs a check on your two '
  'physical computers; no remote hardware performance results are claimed.', 'small')
p('Editable instructions: PERFORMANCE_INSPECTION.md in the repository. Rebuild this PDF with '
  'scripts/build_performance_guide.py using Python with reportlab and DejaVu fonts.', 'small')


def furniture(canvas, doc):
    canvas.saveState()
    canvas.setStrokeColor(BORDER)
    canvas.line(54, 43, 558, 43)
    canvas.setFont('Body', 7.5)
    canvas.setFillColor(MUTED)
    canvas.drawString(54, 30, 'MINI ROVER  |  PERFORMANCE INSPECTION  |  2026-11-05')
    canvas.drawRightString(558, 30, f'{doc.page} / 6')
    canvas.restoreState()


OUTPUT.parent.mkdir(parents=True, exist_ok=True)
doc = SimpleDocTemplate(str(OUTPUT), pagesize=(612, 792), leftMargin=54, rightMargin=54,
                        topMargin=42, bottomMargin=55, title='Mini Rover Performance Inspection - Run Guide',
                        author='Mini Rover project', subject='C2/OC setup, monitoring, recording and replay')
doc.build(story, onFirstPage=furniture, onLaterPages=furniture)
print(OUTPUT)
