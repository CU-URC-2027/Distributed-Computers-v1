# Host ROS time when the complete serial record was received; not device time.
builtin_interfaces/Time stamp
# Unsigned device milliseconds and rising-edge counts wrap at 2^32 and reset
# on reboot. Counts increase in either wheel direction; no signed odometry.
uint32 uptime_ms
uint32 fl_count
uint32 rl_count
uint32 fr_count
uint32 rr_count
# Last UDP commands, not measured velocity or necessarily the applied PWM.
int32 throttle
int32 steering
# State of the common standby pin, not evidence of physical wheel movement.
bool driver_enabled
