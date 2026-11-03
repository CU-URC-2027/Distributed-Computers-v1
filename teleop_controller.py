"""
Xbox Controller Teleoperation Host Script (URC Compliant LED Mode Controls)
Reads Xbox controller joystick and button inputs via Pygame and transmits
UDP drive and status commands to the Arduino Mini Rover.

  -> ROVER AP SSID:  Fleming
  -> ROVER IP ADDR:  192.168.1.50

  [WIFI] Connected to Linksys Blue
 -> ROVER IP ADDR:  10.201.251.114
 -> UDP PORT:       4210


Usage: python teleop_controller.py
"""

import socket
import time
import sys

# Try importing pygame; print friendly error if not installed
try:
    import pygame
except ImportError:
    print("[ERROR] Pygame is not installed. Run 'pip install pygame' on your laptop.")
    sys.exit(1)

# ==========================================
# Network Configuration
# ==========================================
ROVER_IP = "10.201.251.114"  # Arduino UNO R4 WiFi IP address
ROVER_PORT = 4210        # UDP port configured on Arduino firmware

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

# ==========================================
# Gamepad Initialization
# ==========================================
pygame.init()
pygame.joystick.init()

if pygame.joystick.get_count() == 0:
    print("[WARNING] No joystick detected! Plug in your Xbox controller and restart.")
else:
    controller = pygame.joystick.Joystick(0)
    controller.init()
    print(f"[SYSTEM] Connected to Controller: {controller.get_name()}")

print(f"[SYSTEM] Streaming teleop packets to {ROVER_IP}:{ROVER_PORT} (Press Ctrl+C to stop)...")
print("[SYSTEM] URC LED Status Button Mappings:")
print("  - [X Button (Blue)]  -> Teleoperation Mode (Solid Blue LED)")
print("  - [B Button (Red)]   -> Autonomous Mode (Solid Red LED)")
print("  - [A Button (Green)] -> Target Arrival (Flashing Green LED)\n")

# Track current LED mode locally for display
current_mode_str = "TELEOP (BLUE)"
last_button_state = {'a': False, 'b': False, 'x': False}

# ==========================================
# Main Control Loop (~20 Hz)
# ==========================================
try:
    while True:
        # Drain and process event queue so Pygame registers state updates
        for event in pygame.event.get():
            if event.type == pygame.JOYBUTTONDOWN:
                print(f"\n[DEBUG] Button index {event.button} pressed!")

        if pygame.joystick.get_count() > 0:
            # Standard Xbox controller index mapping: 0 = A, 1 = B, 2 = X / 3 = X
            btn_a = controller.get_button(0)  # Green A
            btn_b = controller.get_button(1)  # Red B
            
            # Checks index 2 (standard Windows/USB) and index 3 (standard Bluetooth/Linux)
            btn_x = controller.get_button(2) or (controller.get_numbuttons() > 3 and controller.get_button(3))

            # --- URC LED Mode Switching Logic ---
            # Xbox X Button -> Teleoperation Mode (Solid Blue)
            if btn_x and not last_button_state['x']:
                current_mode_str = "TELEOP (BLUE)"
                sock.sendto(b"MODE:TELEOP\n", (ROVER_IP, ROVER_PORT))
                print("\n[URC MODE SENT] -> Teleoperation (Solid Blue LED)")

            # Xbox B Button -> Autonomous Mode (Solid Red)
            elif btn_b and not last_button_state['b']:
                current_mode_str = "AUTO (RED)"
                sock.sendto(b"MODE:AUTO\n", (ROVER_IP, ROVER_PORT))
                print("\n[URC MODE SENT] -> Autonomous Operation (Solid Red LED)")

            # Xbox A Button -> Successful Target Arrival (Flashing Green)
            elif btn_a and not last_button_state['a']:
                current_mode_str = "ARRIVED (FLASH GRN)"
                sock.sendto(b"MODE:ARRIVED\n", (ROVER_IP, ROVER_PORT))
                print("\n[URC MODE SENT] -> Target Arrival (Flashing Green LED)")

            last_button_state['a'] = bool(btn_a)
            last_button_state['b'] = bool(btn_b)
            last_button_state['x'] = bool(btn_x)

            # --- Joystick Drive Controls (RIGHT STICK) ---
            # Axis 3: Right Stick Vertical (Inverted so forward is positive)
            throttle_raw = -controller.get_axis(3)
            # Axis 2: Right Stick Horizontal (Steering)
            steering_raw = controller.get_axis(2)

            # Map float values (-1.0 to 1.0) to Motor PWM range (-255 to 255)
            throttle = int(throttle_raw * 255)
            steering = int(steering_raw * 255)

            # Deadzone filter to ignore stick drift
            if abs(throttle) < 20: throttle = 0
            if abs(steering) < 20: steering = 0

            # Format drive packet: "throttle,steering\n" (e.g., "180,-45\n")
            packet = f"{throttle},{steering}\n"

            # Transmit via UDP
            sock.sendto(packet.encode('utf-8'), (ROVER_IP, ROVER_PORT))
            print(f"[TELEOP] Mode: {current_mode_str:20s} | Throttle: {throttle:4d} | Steering: {steering:4d}", end='\r')

        time.sleep(0.05)  # 50ms loop delay (20 Packets/sec)

except KeyboardInterrupt:
    print("\n[SYSTEM] Teleop script stopped by user.")
    sock.close()
    pygame.quit()

