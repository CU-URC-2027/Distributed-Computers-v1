// ATmega328P Uno home GPS test. No motors and no extra Arduino libraries.
// GP-20U7: regulated 3.3V supply, GND common, GPS TX -> Uno D4 (RX).
// D5 is SoftwareSerial's unused TX: leave it DISCONNECTED from the GPS.
// USB Serial Monitor / C2 USB GPS baud: 115200. GPS UART: 9600 8N1.
#include <SoftwareSerial.h>
#if !defined(__AVR_ATmega328P__) && !defined(__AVR_ATmega328__)
#error "This sketch targets an ATmega328/328P Uno or classic Nano. Select Arduino Uno."
#endif
SoftwareSerial gpsPort(4, 5);

void setup() {
  Serial.begin(115200);
  gpsPort.begin(9600);
  Serial.println(F("# GP-20U7 RX-only bridge: D4 RX, D5 unconnected, 9600 -> USB 115200"));
}

void loop() {
  while (gpsPort.available()) Serial.write(gpsPort.read());
  // Deliberately no path from USB TX into the GPS; no 5V signal is sent to it.
}
