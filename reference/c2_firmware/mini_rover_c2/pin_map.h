/*
  ================================================================================
  URC 2027 Mini-Rover Master Hardware Pin Map (pin_map.h)
  Target Architecture: Arduino Uno R4 WiFi
  ================================================================================
*/

#ifndef PIN_MAP_H
#define PIN_MAP_H

// --- Motor Drivers (TB6612FNG Dual H-Bridge) ---
#define PIN_MOTOR_L_IN1    2   // Driver 1 AIN1 + BIN1 (Left Direction 1)
#define PIN_LEFT_DIR1      2   
#define PIN_MOTOR_L_PWM    3   // Driver 1 PWMA + PWMB (Left Speed PWM)
#define PIN_LEFT_PWM       3
#define PIN_MOTOR_L_IN2    4   // Driver 1 AIN2 + BIN2 (Left Direction 2)
#define PIN_LEFT_DIR2      4

#define PIN_MOTOR_R_PWM    5   // Driver 2 PWMA + PWMB (Right Speed PWM)
#define PIN_RIGHT_PWM      5
#define PIN_MOTOR_R_IN1    6   // Driver 2 AIN1 + BIN1 (Right Direction 1)
#define PIN_RIGHT_DIR1     6
#define PIN_MOTOR_R_IN2    7   // Driver 2 AIN2 + BIN2 (Right Direction 2)
#define PIN_RIGHT_DIR2     7

#define PIN_MOTOR_STBY     8   // Common Driver Standby Line (HIGH = Active)
#define PIN_DRIVE_STBY     8

// --- Wheel Encoders (Phase A C1 Interrupt Lines) ---
#define PIN_ENC_FL         0   // Front Left Encoder C1
#define PIN_ENC_RL         9   // Rear Left Encoder C1
#define PIN_ENC_FR         11  // Front Right Encoder C1
#define PIN_ENC_RR         13  // Rear Right Encoder C1

// --- Auxiliary & Telemetry ---
#define PIN_NEOPIXEL       12  // URC Smart RGB LED Data Line
#define PIN_BATTERY_SENSE  A1  // Battery Voltage Divider (10k / 3.3k)
#define PIN_SONAR_TRIG     A2  // HC-SR04 Ultrasonic Trigger
#define PIN_SONAR_ECHO     A3  // HC-SR04 Ultrasonic Echo

// --- OLED FeatherWing Tactile Buttons ---
#define PIN_OLED_BTN_A     A0  // Top Button (Prev Screen)
#define PIN_OLED_BTN_B     10  // Middle Button (Next Screen)
#define PIN_OLED_BTN_C     1   // Bottom Button (Mode Action)

#endif // PIN_MAP_H
