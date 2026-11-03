# Rover multi-agent implementation rules

Use three specialists when modifying the C2/OC/MCU route, coordinated by the parent.
Authoritative contract: /home/liamolczak/weeny_ws/INTERFACE_V2.md.
C2 owns mini_rover_c2 and control/c2.py; OC owns mini_rover_oc, control/oc.py and
serial_port.py; Arduino owns the actual mini-rover-prototype sketch, its tests,
control/arduino.py and mini_rover_sim. Coordinator owns shared schemas/contracts,
protocol.py, common fixtures, integration tests and docs. Do not edit another
owner's files without coordinating. Propose contract changes before implementing
incompatible assumptions. Report changed files, tests, and unresolved blockers.

Use OPEN_LOOP_PWM v2, GUI hold-to-drive, one local PC, USB-only motor authority,
PWM <=63. Missing encoders warn; missing controller feedback inhibits. Never label
PWM as velocity or simulation as hardware evidence. Preserve user changes.
Read scripts/run_tests.sh and firmware build/test scripts before running checks.
Run pure/native and isolated ROS/PTY tests before hardware. Do not open the old
firmware with a JSON writer. Physical tests need the documented bench prerequisites;
never invent results. Documentation historical claims do not authorize hardware.
