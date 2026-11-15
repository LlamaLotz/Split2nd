/*
 * reflex_fw — Reflex Trainer hardware unit
 * Board: Arduino Uno R3 (ATmega328P, 5 V, 2 KB SRAM, 32 KB flash)
 * Library: U8g2 by olikraus (page-buffer constructors only)
 *
 * Serial protocol: 115200 8N1, newline-terminated ASCII.
 *
 *    PC -> Uno : PING, VER, DEMO 0|1, GATE <cm>, DIST, STIM,
 *                DISARM, LED G|Y|R|F|0, SHOW <ms> <G|Y|R|F> <best>
 *    Uno -> PC : READY, VER reflex-fw 1.0, PONG, OK, ERR ARGS,
 *                ERR UNKNOWN, ERR LONG, DEMO <cm>, HIT <ms> <cm>, TIMEOUT
 *
 * Design rules:
 *   - No String class, no Serial.parseInt(), no full-buffer U8g2 constructor.
 *   - The ultrasonic is never pinged faster than every 25 ms.
 *   - The OLED is never redrawn while a rep is armed.
 *   - Demo mode is ON by default so the unit is useful with no laptop.
 */

#include <Arduino.h>
#include <Wire.h>
#include <U8g2lib.h>
#include <ctype.h>

/* =====================================================================
 * 1. CONFIG
 * ===================================================================== */

// 1 = SSD1306 128x64 (default), 2 = SH1106 128x64, 3 = SSD1306 128x32
#define OLED_TYPE 1

// If the screen stays blank, try 0x3D instead of 0x3C.
// U8g2 expects the 8-bit address, so the value passed to the library is
// OLED_I2C_ADDR * 2.
#define OLED_I2C_ADDR 0x3C

// Timing
#define DEMO_INTERVAL_MS 150UL
#define PING_INTERVAL_MS 25UL
#define ARM_TIMEOUT_MS   5000UL

// Max printable result / best value. Four digits keeps the 24 px font inside
// the layout and rejects nonsense input.
#define RESULT_MAX_MS 9999UL

// Gate limits
#define GATE_MIN_CM 5
#define GATE_MAX_CM 200
#define GATE_DEFAULT_CM 30

// Longest accepted protocol line (excluding the terminator)
#define LINE_MAX_CHARS 47

/* =====================================================================
 * 2. PINS (source of truth — do not change)
 * ===================================================================== */

const uint8_t PIN_TRIG   = 9;
const uint8_t PIN_ECHO   = 10;
const uint8_t PIN_LED_G  = 5;
const uint8_t PIN_LED_Y  = 6;
const uint8_t PIN_LED_R  = 7;

/* =====================================================================
 * 3. OLED DRIVER
 * ===================================================================== */

#if OLED_TYPE == 1
U8G2_SSD1306_128X64_NONAME_1_HW_I2C u8g2(U8G2_R0, U8X8_PIN_NONE);
#elif OLED_TYPE == 2
U8G2_SH1106_128X64_NONAME_1_HW_I2C u8g2(U8G2_R0, U8X8_PIN_NONE);
#elif OLED_TYPE == 3
U8G2_SSD1306_128X32_UNIVISION_1_HW_I2C u8g2(U8G2_R0, U8X8_PIN_NONE);
#else
#error "OLED_TYPE must be 1 (SSD1306 128x64), 2 (SH1106 128x64) or 3 (SSD1306 128x32)"
#endif

/* =====================================================================
 * 4. STATE
 * ===================================================================== */

// Demo / live mode
bool demo = true;              // demo is ON by default
bool armed = false;            // a rep is being timed
bool hitSent = false;          // one HIT per STIM
bool demoDirty = true;         // force the next demo redraw

// Zoneless distance cache, so the OLED is only redrawn on a visible change
int lastDemoCm = -999;
int lastDemoZone = -999;

// Gate and result values
int gateCm = GATE_DEFAULT_CM;
unsigned int shownMs = 0;
unsigned int bestMs = 0;
char shownGrade = '-';
bool hasResult = false;

// Timing (unsigned subtraction handles millis()/micros() wrap)
unsigned long stimUs = 0;
unsigned long stimMs = 0;
unsigned long lastPingMs = 0;
unsigned long lastDemoMs = 0;

// Serial line buffer — no String, no dynamic allocation
char lineBuf[LINE_MAX_CHARS + 1];
uint8_t lineLen = 0;
bool lineOverflow = false;

// DIST is deferred to the next legal ping slot to avoid echo crosstalk
bool distRequested = false;

enum Zone : int8_t {
  ZONE_NONE = -1,
  ZONE_GREEN = 0,
  ZONE_YELLOW = 1,
  ZONE_RED = 2
};

/* =====================================================================
 * 5. HELPERS
 * ===================================================================== */

static void setLed(char c) {
  // Exactly one LED at a time. 'F' (false start) uses red.
  digitalWrite(PIN_LED_G, (c == 'G') ? HIGH : LOW);
  digitalWrite(PIN_LED_Y, (c == 'Y') ? HIGH : LOW);
  digitalWrite(PIN_LED_R, (c == 'R' || c == 'F') ? HIGH : LOW);
}

static char zoneLed(int zone) {
  if (zone == ZONE_GREEN) return 'G';
  if (zone == ZONE_YELLOW) return 'Y';
  if (zone == ZONE_RED) return 'R';
  return '0';
}

static int zoneFor(int cm) {
  if (cm < 0) return ZONE_NONE;
  if (cm < 20) return ZONE_GREEN;
  if (cm < 40) return ZONE_YELLOW;
  return ZONE_RED;
}

static void printZone(int zone) {
  if (zone == ZONE_GREEN) {
    u8g2.print(F("CLOSE"));
  } else if (zone == ZONE_YELLOW) {
    u8g2.print(F("MID"));
  } else if (zone == ZONE_RED) {
    u8g2.print(F("FAR"));
  } else {
    u8g2.print(F("NO ECHO"));
  }
}

// Returns distance in cm, or -1.0 if no valid echo.
// The caller is responsible for keeping pings at least 25 ms apart.
static float readCm() {
  digitalWrite(PIN_TRIG, LOW);
  delayMicroseconds(2);
  digitalWrite(PIN_TRIG, HIGH);
  delayMicroseconds(10);
  digitalWrite(PIN_TRIG, LOW);

  unsigned long d = pulseIn(PIN_ECHO, HIGH, 12000UL); // 12 ms ~ about 2 m
  if (d == 0) return -1.0f;

  float cm = d * 0.01715f;
  if (cm < 2.0f || cm > 200.0f) return -1.0f;
  return cm;
}

// Strict signed integer parser. No trailing characters allowed.
static bool parseLongArg(const char *s, long &out) {
  bool negative = false;
  if (*s == '+' || *s == '-') {
    negative = (*s == '-');
    s++;
  }
  if (!isdigit((unsigned char)*s)) return false;

  long value = 0;
  while (isdigit((unsigned char)*s)) {
    // Saturate rather than overflow; GATE clamps afterwards anyway.
    if (value <= 100000L) {
      value = value * 10L + (long)(*s - '0');
    }
    s++;
  }
  if (*s != '\0') return false;

  out = negative ? -value : value;
  return true;
}

// Strict unsigned parser with an upper bound.
static bool parseULongArg(const char *&p, unsigned long &out, unsigned long maxValue) {
  if (!isdigit((unsigned char)*p)) return false;

  unsigned long value = 0;
  while (isdigit((unsigned char)*p)) {
    value = value * 10UL + (unsigned long)(*p - '0');
    if (value > maxValue) return false;
    p++;
  }
  out = value;
  return true;
}

// SHOW <ms> <grade> <best>
static bool parseShowArgs(const char *s, unsigned long &ms, char &grade,
                          unsigned long &best) {
  while (*s == ' ') s++;
  if (!parseULongArg(s, ms, RESULT_MAX_MS)) return false;
  if (*s != ' ') return false;

  while (*s == ' ') s++;
  grade = *s++;
  if (grade != 'G' && grade != 'Y' && grade != 'R' && grade != 'F') {
    return false;
  }
  if (*s != ' ') return false;

  while (*s == ' ') s++;
  if (!parseULongArg(s, best, RESULT_MAX_MS)) return false;

  while (*s == ' ') s++;
  return (*s == '\0');
}

/* =====================================================================
 * 6. OLED DRAWING
 * ===================================================================== */

static void drawBootScreen() {
  u8g2.setFont(
#if OLED_TYPE == 3
      u8g2_font_ncenB10_tr
#else
      // logisoso24_tn is digits-only, so the boot text needs the _tr variant.
      u8g2_font_logisoso24_tr
#endif
  );

  int width = (int)u8g2.getUTF8Width("BOOT OK");
  int x = (128 - width) / 2;
  if (x < 0) x = 0;

  u8g2.firstPage();
  do {
#if OLED_TYPE == 3
    u8g2.setFont(u8g2_font_ncenB10_tr);
    u8g2.setCursor((u8g2_uint_t)x, 22);
#else
    u8g2.setFont(u8g2_font_logisoso24_tr);
    u8g2.setCursor((u8g2_uint_t)x, 40);
#endif
    u8g2.print(F("BOOT OK"));
  } while (u8g2.nextPage());
}

static void drawDemoScreen(int cm, int zone) {
  u8g2.firstPage();
  do {
#if OLED_TYPE == 3
    // 128x32: no top row. 16 px number, cm and zone share the row.
    if (cm < 0) {
      u8g2.setFont(u8g2_font_ncenB10_tr);
      u8g2.setCursor(0, 24);
      u8g2.print(F("--"));
    } else {
      u8g2.setFont(u8g2_font_logisoso16_tn);
      u8g2.setCursor(0, 26);
      u8g2.print(cm);
    }

    u8g2.setFont(u8g2_font_6x10_tr);
    u8g2.setCursor(38, 26);
    u8g2.print(F("cm"));
    u8g2.setCursor(70, 26);
    printZone(zone);
#else
    // 128x64: small header, large number, zone footer.
    u8g2.setFont(u8g2_font_6x10_tr);
    u8g2.setCursor(0, 10);
    u8g2.print(F("REFLEX"));
    u8g2.setCursor(80, 10);
    u8g2.print(F("DEMO"));

    if (cm < 0) {
      u8g2.setFont(u8g2_font_ncenB10_tr);
      u8g2.setCursor(0, 48);
      u8g2.print(F("--"));
    } else {
      u8g2.setFont(u8g2_font_logisoso24_tn);
      u8g2.setCursor(0, 50);
      u8g2.print(cm);
    }

    u8g2.setFont(u8g2_font_6x10_tr);
    u8g2.setCursor(80, 50);
    u8g2.print(F("cm"));
    u8g2.setCursor(0, 63);
    printZone(zone);
#endif
  } while (u8g2.nextPage());
}

static void drawResultScreen() {
  u8g2.firstPage();
  do {
#if OLED_TYPE == 3
    u8g2.setFont(u8g2_font_6x10_tr);
    u8g2.setCursor(0, 8);
    u8g2.print(F("REFLEX"));
    u8g2.setCursor(60, 8);
    u8g2.print(F("BEST "));
    u8g2.print(bestMs);

    u8g2.setFont(u8g2_font_logisoso16_tn);
    u8g2.setCursor(0, 26);
    u8g2.print(shownMs);

    u8g2.setFont(u8g2_font_6x10_tr);
    u8g2.setCursor(52, 26);
    u8g2.print(F("ms"));

    u8g2.setFont(u8g2_font_ncenB10_tr);
    u8g2.setCursor(116, 26);
    u8g2.print(shownGrade);
#else
    u8g2.setFont(u8g2_font_6x10_tr);
    u8g2.setCursor(0, 10);
    u8g2.print(F("REFLEX"));
    u8g2.setCursor(60, 10);
    u8g2.print(F("BEST "));
    u8g2.print(bestMs);

    u8g2.setFont(u8g2_font_logisoso24_tn);
    u8g2.setCursor(0, 50);
    u8g2.print(shownMs);

    u8g2.setFont(u8g2_font_6x10_tr);
    u8g2.setCursor(88, 50);
    u8g2.print(F("ms"));
    u8g2.setCursor(108, 63);
    u8g2.print(shownGrade);
#endif
  } while (u8g2.nextPage());
}

static void redrawIdleOrResult() {
  if (armed) return; // never block an active rep with I2C
  // The idle screen is the result screen with its reset values.
  drawResultScreen();
}

/* =====================================================================
 * 7. COMMAND HANDLING
 * ===================================================================== */

static void handleLine(char *line) {
  // Capture the receive timestamp before doing any work. STIM must anchor
  // the timer here, not after parsing or drawing.
  unsigned long rxUs = micros();
  unsigned long rxMs = millis();

  if (!strcmp(line, "PING")) {
    Serial.println(F("PONG"));
    return;
  }

  if (!strcmp(line, "VER")) {
    Serial.println(F("VER reflex-fw 1.0"));
    return;
  }

  if (!strcmp(line, "STIM")) {
    // Turn demo off and LEDs off, then arm.
    demo = false;
    demoDirty = true;
    setLed('0');

    armed = true;
    hitSent = false;
    stimUs = rxUs;
    stimMs = rxMs;
    lastPingMs = rxMs;       // first armed ping happens 25 ms later
    distRequested = false;   // a stale DIST must not steal the first slot

    Serial.println(F("OK"));
    return;
  }

  if (!strcmp(line, "DISARM")) {
    armed = false;
    Serial.println(F("OK"));
    return;
  }

  if (!strcmp(line, "DEMO") || !strncmp(line, "DEMO ", 5)) {
    if (!strcmp(line, "DEMO 1")) {
      demo = true;
      demoDirty = true;
      armed = false;         // avoid demo + armed pings running together
      hitSent = false;
      distRequested = false;
      lastDemoMs = millis() - DEMO_INTERVAL_MS; // redraw on the next loop
      Serial.println(F("OK"));
    } else if (!strcmp(line, "DEMO 0")) {
      demo = false;
      demoDirty = true;
      setLed('0');
      if (!armed) redrawIdleOrResult();
      Serial.println(F("OK"));
    } else {
      Serial.println(F("ERR ARGS"));
    }
    return;
  }

  if (!strcmp(line, "GATE") || !strncmp(line, "GATE ", 5)) {
    long value;
    if (line[4] != ' ' || !parseLongArg(line + 5, value)) {
      Serial.println(F("ERR ARGS"));
      return;
    }
    if (value < GATE_MIN_CM) value = GATE_MIN_CM;
    if (value > GATE_MAX_CM) value = GATE_MAX_CM;
    gateCm = (int)value;
    Serial.println(F("OK"));
    return;
  }

  if (!strcmp(line, "LED") || !strncmp(line, "LED ", 4)) {
    if (line[3] != ' ' || line[4] == '\0' || line[5] != '\0') {
      Serial.println(F("ERR ARGS"));
      return;
    }
    char c = line[4];
    if (c != 'G' && c != 'Y' && c != 'R' && c != 'F' && c != '0') {
      Serial.println(F("ERR ARGS"));
      return;
    }
    demo = false;
    demoDirty = true;
    setLed(c);
    Serial.println(F("OK"));
    return;
  }

  if (!strcmp(line, "SHOW") || !strncmp(line, "SHOW ", 5)) {
    unsigned long ms;
    unsigned long best;
    char grade;

    if (line[4] != ' ' || !parseShowArgs(line + 5, ms, grade, best)) {
      Serial.println(F("ERR ARGS"));
      return;
    }

    bool wasDemo = demo;
    demo = false;
    demoDirty = true;
    // Turn the LEDs off only if demo was driving them. In live mode the
    // backend sends LED <grade> immediately before SHOW; do not erase it.
    if (wasDemo) setLed('0');
    armed = false;
    hitSent = false;

    shownMs = (unsigned int)ms;
    shownGrade = grade;
    bestMs = (unsigned int)best;
    hasResult = true;

    drawResultScreen();
    Serial.println(F("OK"));
    return;
  }

  if (!strcmp(line, "DIST")) {
    // Reply is emitted by loop() on the next legal ping slot.
    distRequested = true;
    return;
  }

  Serial.println(F("ERR UNKNOWN"));
}

// Non-blocking line reader. Accepts '\n' and "\r\n"; overlong lines are
// discarded and answered once with ERR LONG.
static void pollSerial() {
  while (Serial.available() > 0) {
    char c = (char)Serial.read();

    if (c == '\r' || c == '\n') {
      if (lineOverflow) {
        Serial.println(F("ERR LONG"));
        lineOverflow = false;
        lineLen = 0;
      } else if (lineLen > 0) {
        lineBuf[lineLen] = '\0';
        lineLen = 0;
        handleLine(lineBuf);
      }
    } else if (!lineOverflow) {
      if (lineLen < LINE_MAX_CHARS) {
        lineBuf[lineLen++] = c;
      } else {
        // The 48th character overflows the 47-character limit.
        lineOverflow = true;
        lineLen = 0;
      }
    }
  }
}

/* =====================================================================
 * 8. PERIODIC SERVICES
 * ===================================================================== */

static void serviceDist(unsigned long now) {
  if (!distRequested) return;
  if ((now - lastPingMs) < PING_INTERVAL_MS) return;

  distRequested = false;
  lastPingMs = now;

  float cm = readCm();
  int cmInt = (cm < 0.0f) ? -1 : (int)cm;

  Serial.print(F("DIST "));
  Serial.println(cmInt);
}

static void serviceArmed(unsigned long now) {
  if (!armed) return;

  if (!hitSent && (now - stimMs) > ARM_TIMEOUT_MS) {
    armed = false;
    Serial.println(F("TIMEOUT"));
    return;
  }

  if (hitSent) return;
  if ((now - lastPingMs) < PING_INTERVAL_MS) return;

  lastPingMs = now;

  float cm = readCm();
  if (cm >= 2.0f && cm < (float)gateCm) {
    unsigned long dtMs = (micros() - stimUs) / 1000UL;
    Serial.print(F("HIT "));
    Serial.print(dtMs);
    Serial.print(' ');
    Serial.println((int)cm);
    hitSent = true;
  }
}

static void serviceDemo(unsigned long now) {
  if (!demo || armed) return;
  if ((now - lastDemoMs) < DEMO_INTERVAL_MS) return;
  // Respect the sensor's minimum ping spacing even after a mode switch.
  if ((now - lastPingMs) < PING_INTERVAL_MS) return;

  lastDemoMs = now;
  lastPingMs = now;

  float cm = readCm();
  int cmInt = (cm < 0.0f) ? -1 : (int)cm;
  int zone = zoneFor(cmInt);

  setLed(zoneLed(zone));

  Serial.print(F("DEMO "));
  Serial.println(cmInt);

  if ((demoDirty || cmInt != lastDemoCm || zone != lastDemoZone) &&
      Serial.available() == 0) {
    demoDirty = false;
    lastDemoCm = cmInt;
    lastDemoZone = zone;
    drawDemoScreen(cmInt, zone);
  }
}

/* =====================================================================
 * 9. SETUP
 * ===================================================================== */

static void i2cScan() {
  uint8_t found = 0;
  Wire.begin();

  for (uint8_t addr = 0x08; addr <= 0x77; addr++) {
    Wire.beginTransmission(addr);
    if (Wire.endTransmission() == 0) {
      Serial.print(F("I2C 0x"));
      if (addr < 0x10) Serial.print('0');
      Serial.println(addr, HEX);
      found++;
    }
  }

  if (found == 0) {
    Serial.println(F("I2C NONE"));
  }
}

void setup() {
  // Pins first, so the unit is in a safe state even if the OLED is missing.
  pinMode(PIN_TRIG, OUTPUT);
  digitalWrite(PIN_TRIG, LOW);
  pinMode(PIN_ECHO, INPUT);
  pinMode(PIN_LED_G, OUTPUT);
  pinMode(PIN_LED_Y, OUTPUT);
  pinMode(PIN_LED_R, OUTPUT);
  setLed('0');

  Serial.begin(115200);

  // Debug aid for a blank screen.
  i2cScan();

  // U8g2 needs the 8-bit I2C address. Set it BEFORE begin() so the init
  // sequence reaches a panel at 0x3D as well as one at 0x3C.
  u8g2.setI2CAddress((uint8_t)(OLED_I2C_ADDR * 2));
  u8g2.begin();
  u8g2.setBusClock(400000);

  drawBootScreen();

  // Visible self test: green, yellow, red, 250 ms each.
  setLed('G');
  delay(250);
  setLed('Y');
  delay(250);
  setLed('R');
  delay(250);
  setLed('0');

  // Default runtime state: demo on, no result yet.
  demo = true;
  demoDirty = true;
  armed = false;
  hitSent = false;
  hasResult = false;
  gateCm = GATE_DEFAULT_CM;
  lastDemoCm = -999;
  lastDemoZone = -999;
  lastDemoMs = millis() - DEMO_INTERVAL_MS; // first demo tick immediately
  lastPingMs = millis() - PING_INTERVAL_MS;  // allow that first ping
  distRequested = false;

  Serial.println(F("READY"));
  Serial.println(F("VER reflex-fw 1.0"));
}

/* =====================================================================
 * 10. LOOP
 * ===================================================================== */

void loop() {
  pollSerial();

  unsigned long now = millis();

  // DIST goes first, but only on a legal ping slot.
  serviceDist(now);

  // A rep always has priority over the demo screen.
  serviceArmed(now);

  // Demo only runs when not armed; armed service may have just timed out.
  serviceDemo(now);
}
