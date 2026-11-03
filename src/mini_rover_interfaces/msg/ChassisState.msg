# stamp is OC receipt wall time for audit. sample_time_ms is MCU uptime.
builtin_interfaces/Time stamp
uint16 protocol_version
string mode
string arduino_boot_id
string source_session
uint64 sample_seq
uint32 sample_time_ms
uint64 last_applied_command_id
uint64 last_applied_seq
int16 applied_left_pwm
int16 applied_right_pwm
float64 mcu_age_ms
WheelState front_left
WheelState rear_left
WheelState front_right
WheelState rear_right
bool simulated
