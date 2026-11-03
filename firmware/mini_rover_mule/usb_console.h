#pragma once

#include <Arduino.h>

// UNO R4 WiFi's default Serial is a blocking UART through the ESP32 USB bridge.
// Use the RA4M1 native CDC endpoint on the same USB-C connector instead. Do not
// undefine NO_USB: WiFiS3 still needs the board's original Serial2 UART mapping.
// __USBStart() switches the onboard USB mux; the Linux device identity changes.
class UsbConsole : public Print {
 public:
  void begin(unsigned long baud) {
    __USBStart();
    SerialUSB.begin(baud);
  }

  int available() { return SerialUSB.available(); }
  int read() { return SerialUSB.read(); }
  explicit operator bool() { return static_cast<bool>(SerialUSB); }
  using Print::write;

  size_t write(uint8_t value) override { return write(&value, 1); }

  size_t write(const uint8_t* data, size_t size) override {
    // Only this main-loop console writes CDC, so free space cannot be consumed
    // by another writer between this check and the write. USB may only free it.
    if (!SerialUSB || SerialUSB.availableForWrite() < static_cast<int>(size)) {
      return 0;
    }
    return SerialUSB.write(data, size);
  }
};

