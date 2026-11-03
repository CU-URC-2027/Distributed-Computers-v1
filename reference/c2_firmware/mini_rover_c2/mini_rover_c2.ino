/*
char ssid[] = C2_WIFI_SSID;       // Wi-Fi SSID
char pass[] = C2_WIFI_PASSWORD;          // Wi-Fi Password
unsigned int localPort = 4210;     // UDP Port to listen on

================================================================================
  URC 2027 Mini-Rover Firmware (v6 - SH1107 OLED & NeoPixel Timing Fix)
  ================================================================================
  Target Architecture: Arduino Uno R4 WiFi
  
  Updates in v6:
    - Updated OLED driver to Adafruit_SH1107 to eliminate display static/noise.
    - Added display.setRotation(1) for proper 128x64 landscape orientation.
    - Updated display macros to SH110X_WHITE.
    - Optimized updateUrcLed() state tracking to eliminate constant pixel.show()
      calls, preventing UDP packet drops and encoder interrupt misses.
  ================================================================================
*/

#include <Wire.h>
#include <WiFiS3.h>
#include <WiFiUdp.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SH110X.h>
#include <Adafruit_NeoPixel.h>
#include <Adafruit_MPU6050.h>
#include <Adafruit_Sensor.h>
#include <FspLinkIrq.h>

#include "pin_map.h"
#include "wifi_credentials.h"
#include "C2Guard.h"
#include "c2_config.h"
#if C2_ENABLE_GPS
// REWIRE first: FL encoder D0 -> A2; OLED C button D1 -> A3.
// Sonar is disabled in this configuration. Serial1 owns BOTH D0 and D1.
#undef PIN_ENC_FL
#undef PIN_OLED_BTN_C
#define PIN_ENC_FL A2
#define PIN_OLED_BTN_C A3
#endif

// ================================================================================
// WI-FI & UDP TELEOPERATION CONFIGURATION
// ================================================================================
char ssid[] = C2_WIFI_SSID;       // Wi-Fi SSID
char pass[] = C2_WIFI_PASSWORD;          // Wi-Fi Password
unsigned int localPort = 4210;     // UDP Port to listen on

WiFiUDP Udp;
char packetBuffer[255];
IPAddress roverIP;

// ================================================================================
// URC LED MODES & HARDWARE INSTANCES
// ================================================================================
enum UrcLedMode {
  MODE_TELEOP,   // Solid Blue (Manually driving)
  MODE_AUTO,     // Solid Red (Autonomous operation)
  MODE_ARRIVED   // Flashing Green (Target arrived)
};

UrcLedMode currentLedMode = MODE_TELEOP;

// Hardware Drivers
Adafruit_NeoPixel pixel(1, PIN_NEOPIXEL, NEO_GRB + NEO_KHZ800);

// Using Wire1 for Arduino Uno R4 WiFi Qwiic STEMMA QT Connector with SH1107 OLED
Adafruit_SH1107 display = Adafruit_SH1107(64, 128, &Wire1);
Adafruit_MPU6050 mpu;

bool oledConnected = false;
bool mpuConnected = false;
int oledScreenPage = 0; // 0=Power/Network, 1=IMU, 2=Motors/Encoders, 3=Sonar

// Encoder Pulse Counters
volatile uint32_t countFL = 0, countRL = 0, countFR = 0, countRR = 0;
bool encoderAvailable[4] = {false,false,false,false};

// Non-blocking Timing Variables
unsigned long lastOledUpdate = 0;
unsigned long lastGreenFlash = 0;
bool greenFlashState = false;

// Driver Telemetry
int currentThrottle = 0;
int currentSteering = 0;

// Interrupt Service Routines
void ISR_FL() { countFL++; }
void ISR_RL() { countRL++; }
void ISR_FR() { countFR++; }
void ISR_RR() { countRR++; }

C2Guard c2;
IPAddress c2PeerIp;
uint16_t c2PeerPort=0;
bool haveC2Peer=false;
uint32_t c2StateId=0, lastC2Telemetry=0;
void applyC2Drive();
void sendC2State();
void relayGps();

// Function Declarations
void handleUdpPackets();
void handleSerialCommands();
void handleOledButtons();
void updateUrcLed();
void executeMotorDrive(int throttle, int steering);
void stopMotors();
void renderOledTelemetry();

// ================================================================================
// SETUP
// ================================================================================
void setup() {
  // Establish standby before any serial, display or network startup waits.
  pinMode(PIN_MOTOR_STBY, OUTPUT);
  digitalWrite(PIN_MOTOR_STBY, LOW);
  Serial.begin(115200);
  delay(1000); // Give Serial Monitor time to connect

  Serial.println(F("\n=============================================="));
  Serial.println(F("   URC MINI-ROVER C2 GUARD V1 (STARTS DISABLED)    "));
  Serial.println(F("=============================================="));

  // Initialize Pin Modes
  pinMode(PIN_MOTOR_L_IN1, OUTPUT);
  pinMode(PIN_MOTOR_L_PWM, OUTPUT);
  pinMode(PIN_MOTOR_L_IN2, OUTPUT);
  pinMode(PIN_MOTOR_R_PWM, OUTPUT);
  pinMode(PIN_MOTOR_R_IN1, OUTPUT);
  pinMode(PIN_MOTOR_R_IN2, OUTPUT);
  pinMode(PIN_MOTOR_STBY, OUTPUT);

  digitalWrite(PIN_MOTOR_STBY, LOW); // Drivers in Standby

#if !C2_ENABLE_GPS
  pinMode(PIN_SONAR_TRIG, OUTPUT);
  pinMode(PIN_SONAR_ECHO, INPUT);
#else
  Serial1.begin(9600);
#endif

  pinMode(PIN_OLED_BTN_A, INPUT_PULLUP);
  pinMode(PIN_OLED_BTN_B, INPUT_PULLUP);
  pinMode(PIN_OLED_BTN_C, INPUT_PULLUP);

  // Encoder Interrupts
  pinMode(PIN_ENC_FL, INPUT_PULLUP);
  pinMode(PIN_ENC_RL, INPUT_PULLUP);
  pinMode(PIN_ENC_FR, INPUT_PULLUP);
  pinMode(PIN_ENC_RR, INPUT_PULLUP);

  attachInterrupt(digitalPinToInterrupt(PIN_ENC_FL), ISR_FL, RISING);
  attachInterrupt(digitalPinToInterrupt(PIN_ENC_RL), ISR_RL, RISING);
  attachInterrupt(digitalPinToInterrupt(PIN_ENC_FR), ISR_FR, RISING);
  attachInterrupt(digitalPinToInterrupt(PIN_ENC_RR), ISR_RR, RISING);
  const int encoderPins[4]={PIN_ENC_FL,PIN_ENC_RL,PIN_ENC_FR,PIN_ENC_RR};
  for(int i=0;i<4;++i) {
    encoderAvailable[i]=getIrqIndexFromPin(encoderPins[i])>=0;
    if(!encoderAvailable[i]) {
      Serial.print(F("[ENCODER] No interrupt on pin ")); Serial.println(encoderPins[i]);
    }
  }

  // Initialize NeoPixel
  pixel.begin();
  pixel.setBrightness(50);
  updateUrcLed();

  // Initialize Wire1 (Uno R4 WiFi Qwiic Bus)
  Wire1.begin();

  // Initialize SH1107 OLED on Wire1 (Address 0x3C)
  if (display.begin(0x3C, true)) {
    oledConnected = true;
    display.setRotation(1); // Set to 128x64 landscape orientation
    display.clearDisplay();
    display.setTextSize(1);
    display.setTextColor(SH110X_WHITE);
    display.setCursor(0, 0);
    display.println(F("URC MINI-ROVER v6"));
    display.println(F("OLED: SH1107 Wire1 OK"));
    display.println(F("Initializing..."));
    display.display();
    Serial.println(F("[OLED] SH1107 connected successfully on Qwiic (&Wire1) @ 0x3C"));
  } else {
    Serial.println(F("[OLED WARNING] SH1107 display not found on Wire1 0x3C!"));
  }

  // Initialize MPU6050 on standard Wire bus (Pins A4/A5)
  Wire.begin();
  if (mpu.begin()) {
    mpuConnected = true;
    mpu.setAccelerometerRange(MPU6050_RANGE_8_G);
    mpu.setGyroRange(MPU6050_RANGE_500_DEG);
    mpu.setFilterBandwidth(MPU6050_BAND_21_HZ);
    Serial.println(F("[IMU] MPU6050 Connected on Wire (A4/A5)"));
  } else {
    Serial.println(F("[IMU WARNING] MPU6050 not detected on A4/A5"));
  }

  // Connect to existing Home Wi-Fi Router
  Serial.print(F("[WIFI] Connecting to Home Router: "));
  Serial.println(ssid);
  
  WiFi.begin(ssid, pass);
  
  // Wait for connection
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(F("."));
  }
  
  Udp.begin(localPort);
  Serial.println(F("\n[WIFI] Connected to Home Network!"));
  Serial.print(F(" -> ROVER IP ADDR:  ")); Serial.println(WiFi.localIP());
  Serial.print(F(" -> UDP PORT:       ")); Serial.println(localPort);

  Serial.println(F("=============================================="));
  Serial.println(F("Ready: C2V1 guarded UDP. Legacy drive/enable commands rejected."));
}

// ================================================================================
// MAIN LOOP
// ================================================================================
void loop() {
  c2.tick(millis());
  applyC2Drive();
  if (WiFi.status() != WL_CONNECTED) {
    c2.disable("WIFI_LOST");
    applyC2Drive();
    return; // Do not enable from any buffered packet while the link is down.
  }
  handleUdpPackets();
  relayGps();
  c2.tick(millis());
  applyC2Drive();
  if (haveC2Peer && millis()-lastC2Telemetry >= 100) sendC2State();
  handleSerialCommands();
  handleOledButtons();
  updateUrcLed();

  // Refresh OLED telemetry every 150ms if display detected
  if (oledConnected && (millis() - lastOledUpdate > 150)) {
    lastOledUpdate = millis();
    renderOledTelemetry();
  }
}

// ================================================================================
// URC LED MODE HANDLER (STATE-CHANGE OPTIMIZED)
// ================================================================================
void updateUrcLed() {
  static int lastRenderedMode = -1;

  if (currentLedMode == MODE_ARRIVED) {
    if (millis() - lastGreenFlash > 250) { // Flashing Green
      lastGreenFlash = millis();
      greenFlashState = !greenFlashState;
      pixel.setPixelColor(0, greenFlashState ? pixel.Color(0, 255, 0) : pixel.Color(0, 0, 0));
      pixel.show();
    }
    lastRenderedMode = MODE_ARRIVED;
  } 
  else if (currentLedMode != lastRenderedMode) {
    if (currentLedMode == MODE_TELEOP) {
      pixel.setPixelColor(0, pixel.Color(0, 0, 255)); // Solid Blue
    } else if (currentLedMode == MODE_AUTO) {
      pixel.setPixelColor(0, pixel.Color(255, 0, 0)); // Solid Red
    }
    pixel.show();
    lastRenderedMode = currentLedMode;
  }
}

// ================================================================================
// UDP PACKET PARSER
// ================================================================================
void applyC2Drive() {
  if (!c2.enabled || c2.stopped) {
    digitalWrite(PIN_MOTOR_STBY, LOW);
    stopMotors();
    currentThrottle=currentSteering=0;
  } else {
    currentThrottle=c2.throttle;
    currentSteering=c2.steering;
    executeMotorDrive(currentThrottle,currentSteering);
    digitalWrite(PIN_MOTOR_STBY,HIGH);
  }
}

void sendC2State() {
  if (!haveC2Peer) return;
  noInterrupts();
  uint32_t fl=countFL, rl=countRL, fr=countFR, rr=countRR;
  interrupts();
  uint32_t counts[4]={fl,rl,fr,rr};
  char enc[4][16];
  for(int i=0;i<4;++i) {
    if(encoderAvailable[i]) snprintf(enc[i],sizeof(enc[i]),"%lu",(unsigned long)counts[i]);
    else strcpy(enc[i],"null"); // Unsupported interrupt is NOT a zero-speed measurement.
  }
  char response[480];
  int n=snprintf(response,sizeof(response),
    "{\"v\":1,\"type\":\"state\",\"fw\":\"c2-guard-v1\",\"session\":\"%s\",\"seq\":%lu,\"epoch\":%lu,\"state_id\":%lu,\"enabled\":%s,\"stopped\":%s,\"throttle\":%d,\"steering\":%d,\"encoders\":[%s,%s,%s,%s],\"reason\":\"%s\",\"watchdog_ms\":250}",
    c2.session,(unsigned long)c2.seq,(unsigned long)c2.epoch,(unsigned long)++c2StateId,
    c2.enabled?"true":"false",c2.stopped?"true":"false",c2.throttle,c2.steering,
    enc[0],enc[1],enc[2],enc[3],c2.reason);
  if (n>0 && (size_t)n<sizeof(response)) {
    Udp.beginPacket(c2PeerIp,c2PeerPort);
    Udp.write((const uint8_t*)response,n);
    Udp.endPacket();
  }
  lastC2Telemetry=millis();
}

void handleUdpPackets() {
  // Bounded work: do not allow a packet flood to starve the watchdog.
  for (int budget=0; budget<4; ++budget) {
    c2.tick(millis()); applyC2Drive();
    int size=Udp.parsePacket();
    if (!size) return;
    IPAddress ip=Udp.remoteIP(); uint16_t port=Udp.remotePort();
    int n=Udp.read(packetBuffer,sizeof(packetBuffer)-1);
    if (n<=0) continue;
    packetBuffer[n]=0;
    if (size!=n) { while(Udp.available()) Udp.read(); continue; }
    // A legacy stop remains useful; no legacy packet can enable this firmware.
    if (!strcmp(packetBuffer,"MODE:ARRIVED\n") || !strcmp(packetBuffer,"MODE:ARRIVED")) {
      c2.disable("LEGACY_STOP",true); applyC2Drive(); sendC2State(); continue;
    }
    C2Packet p;
    if (!c2parse(packetBuffer,(size_t)n,p)) continue;
    bool samePeer=haveC2Peer && ip==c2PeerIp && port==c2PeerPort;
    if (strcmp(p.op,"HELLO") && !samePeer) continue;
    if (haveC2Peer && !strcmp(p.session,c2.session) && !samePeer) continue;
    if (c2.accept(p,millis())) {
      c2PeerIp=ip; c2PeerPort=port; haveC2Peer=true;
      currentLedMode=c2.enabled?MODE_TELEOP:MODE_ARRIVED;
      applyC2Drive(); sendC2State();
    }
  }
}

void relayGps() {
#if C2_ENABLE_GPS
  static char line[121]; static uint8_t used=0;
  for(int budget=0;budget<64 && Serial1.available();++budget) {
    char c=(char)Serial1.read();
    if(c=='$') { used=0; line[used++]=c; continue; }
    if(c=='\r') continue;
    if(c=='\n') {
      line[used]=0;
      if(used && haveC2Peer) {
        char message[240];
        int n=snprintf(message,sizeof(message),"{\"v\":1,\"type\":\"gps_nmea\",\"session\":\"%s\",\"nmea\":\"%s\"}",c2.session,line);
        if(n>0 && (size_t)n<sizeof(message)) {
          Udp.beginPacket(c2PeerIp,c2PeerPort); Udp.write((const uint8_t*)message,n); Udp.endPacket();
        }
      }
      used=0;
    } else if(used && used<120 && ((c>='0'&&c<='9') || (c>='A'&&c<='Z') || c==',' || c=='.' || c=='*' || c=='-' || c=='+')) line[used++]=c;
    else used=0;
  }
#endif
}

// ================================================================================
// SERIAL COMMAND PARSER
// ================================================================================
void handleSerialCommands() {
  if (Serial.available()) {
    char c = Serial.read();
    if (c == 'x' || c == 'X') {
      currentLedMode = MODE_TELEOP;
      Serial.println(F("[MODE] Switched to Teleop Mode (Solid Blue)"));
    } else if (c == 'b' || c == 'B') {
      currentLedMode = MODE_AUTO;
      Serial.println(F("[MODE] Switched to Autonomous Mode (Solid Red)"));
    } else if (c == 'a' || c == 'A') {
      currentLedMode = MODE_ARRIVED;
      Serial.println(F("[MODE] Target Arrival Triggered (Flashing Green)"));
    }
  }
}

// ================================================================================
// MOTOR DRIVE CONTROL
// ================================================================================
void executeMotorDrive(int throttle, int steering) {
  int leftSpeed = constrain(throttle + steering, -255, 255);
  int rightSpeed = constrain(throttle - steering, -255, 255);

  digitalWrite(PIN_MOTOR_L_IN1, leftSpeed >= 0 ? HIGH : LOW);
  digitalWrite(PIN_MOTOR_L_IN2, leftSpeed >= 0 ? LOW : HIGH);
  analogWrite(PIN_MOTOR_L_PWM, abs(leftSpeed));

  digitalWrite(PIN_MOTOR_R_IN1, rightSpeed >= 0 ? HIGH : LOW);
  digitalWrite(PIN_MOTOR_R_IN2, rightSpeed >= 0 ? LOW : HIGH);
  analogWrite(PIN_MOTOR_R_PWM, abs(rightSpeed));
}

void stopMotors() {
  analogWrite(PIN_MOTOR_L_PWM, 0);
  analogWrite(PIN_MOTOR_R_PWM, 0);
}

// ================================================================================
// OLED BUTTON HANDLING
// ================================================================================
void handleOledButtons() {
  static bool lastBtnA = HIGH, lastBtnB = HIGH, lastBtnC = HIGH;
  bool btnA = digitalRead(PIN_OLED_BTN_A);
  bool btnB = digitalRead(PIN_OLED_BTN_B);
  bool btnC = digitalRead(PIN_OLED_BTN_C);

  if (btnA == LOW && lastBtnA == HIGH) oledScreenPage = (oledScreenPage + 3) % 4;
  if (btnB == LOW && lastBtnB == HIGH) oledScreenPage = (oledScreenPage + 1) % 4;
  if (btnC == LOW && lastBtnC == HIGH) {
    if (currentLedMode == MODE_TELEOP) currentLedMode = MODE_AUTO;
    else if (currentLedMode == MODE_AUTO) currentLedMode = MODE_ARRIVED;
    else currentLedMode = MODE_TELEOP;
  }

  lastBtnA = btnA; lastBtnB = btnB; lastBtnC = btnC;
}

// ================================================================================
// OLED RENDERING
// ================================================================================
void renderOledTelemetry() {
  display.clearDisplay();
  display.setTextSize(1);

  float rawA1 = analogRead(PIN_BATTERY_SENSE);
  float batteryVoltage = ((rawA1 / 1023.0) * 5.0) * (13.3 / 3.3);

  switch (oledScreenPage) {
    case 0:
      display.setCursor(0, 0);  display.println(F("=== POWER & NETWORK ==="));
      display.setCursor(0, 14); display.print(F("IP:  ")); display.println(WiFi.localIP());
      display.setCursor(0, 26); display.print(F("Bat: ")); display.print(batteryVoltage, 2); display.println(F(" V"));
      display.setCursor(0, 38); display.print(F("LED: "));
      if (currentLedMode == MODE_TELEOP) display.println(F("BLUE (TELEOP)"));
      else if (currentLedMode == MODE_AUTO) display.println(F("RED (AUTO)"));
      else display.println(F("FLASH GREEN (ARRIVED)"));
      break;

    case 1:
      display.setCursor(0, 0); display.println(F("=== MPU6050 MOTION ==="));
      if (mpuConnected) {
        sensors_event_t a, g, temp;
        mpu.getEvent(&a, &g, &temp);
        display.setCursor(0, 16);
        display.print(F("Accel X: ")); display.print(a.acceleration.x, 1); display.println(F(" m/s2"));
        display.print(F("Accel Y: ")); display.print(a.acceleration.y, 1); display.println(F(" m/s2"));
        display.print(F("Gyro  Z: ")); display.print(g.gyro.z, 1); display.println(F(" rad/s"));
      } else {
        display.setCursor(0, 24); display.println(F("IMU Status: N/C"));
      }
      break;

    case 2:
      display.setCursor(0, 0); display.println(F("=== WHEEL ENCODERS ==="));
      display.setCursor(0, 16);
      display.print(F("FL: ")); display.print(countFL); display.print(F("  FR: ")); display.println(countFR);
      display.print(F("RL: ")); if(encoderAvailable[1]) display.print(countRL); else display.print("N/A");
      display.print(F("  RR: ")); if(encoderAvailable[3]) display.println(countRR); else display.println("N/A");
      display.setCursor(0, 40);
      display.print(F("STBY Pin: ")); display.println(digitalRead(PIN_MOTOR_STBY) ? "ENABLED" : "STANDBY");
      break;

    case 3:
#if C2_ENABLE_GPS
      display.setCursor(0, 0); display.println(F("GPS UART D0/D1 ACTIVE"));
      display.println(F("FL encoder -> A2"));
      display.println(F("OLED button C -> A3"));
      display.println(F("SONAR DISABLED"));
      break;
#endif
      display.setCursor(0, 0); display.println(F("=== HC-SR04 SONAR ==="));
      display.setCursor(0, 24); display.println(F("Sonar: Pins A2/A3 Configured"));
      display.println(F("Ready for sensor plug-in"));
      break;
  }

  display.drawFastHLine(0, 54, 128, SH110X_WHITE);
  display.setCursor(0, 56);
  display.print(F("P")); display.print(oledScreenPage + 1); display.print(F("/4 | Btn A/B:Nav C:Mode"));
  display.display();
}
