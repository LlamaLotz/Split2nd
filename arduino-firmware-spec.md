# Arduino Firmware Spec: Reflex Trainer Hardware Unit

> **Instruction to Claude Code:** Read this whole file, then write the complete, compile-ready Arduino firmware described here as ONE sketch file (`reflex_fw/reflex_fw.ino`), plus a small Python test script (`test_rig.py`). Do not ask questions unless something is truly missing. Where this file gives a default, use it. Compile it before you finish. Do not leave TODOs. The sketch must work on the first upload.

---

## 1. What this is

A hackathon project: a reflex trainer. A laptop runs the app (webcam, web UI). This Arduino is the **physical I/O unit** next to the laptop. It does exactly four things:

1. **Measures distance** with an ultrasonic sensor and times when something enters a distance "gate" after a stimulus.
2. **Shows results** on a small OLED.
3. **Shows green / yellow / red feedback** with three LEDs.
4. **Talks to the laptop over USB serial** with a simple text protocol.

It also has a **standalone DEMO mode** (on by default after boot) so the hardware can be tested without any laptop software: wave a hand in front of the sensor and the LEDs and OLED react immediately.

There is **no rotary encoder, no buzzer, no buttons, no other hardware**. Do not add any.

---

## 2. Hardware (exact, verified)

| Part | Details |
|---|---|
| Board | Arduino Uno R3 (ATmega328P, 5 V logic, 2 KB RAM, 32 KB flash) |
| Ultrasonic | HC-SR04 style, 4 pins: VCC, TRIG, ECHO, GND. Powered from 5 V. NOT an I2C sensor. |
| LEDs | 3 LEDs: green, yellow, red. Each has a 220 ohm series resistor, anode side to the Arduino pin, cathode to GND. So **HIGH = LED on**. |
| OLED | I2C OLED with 4 pins in the order SDA, SCK(=SCL), VCC, GND. Almost certainly an SSD1306 (some 1.3 inch modules are SH1106). Size is probably 128x64 but could be 128x32. I2C address is almost certainly 0x3C. |
| Power | USB from the laptop. 5 V rail feeds the ultrasonic and the OLED. |

### Pin map (source of truth, do not change)

| Function | Arduino pin |
|---|---|
| Ultrasonic TRIG | D9 |
| Ultrasonic ECHO | D10 |
| LED green | D5 |
| LED yellow | D6 |
| LED red | D7 |
| OLED SDA | A4 (hardware I2C) |
| OLED SCL | A5 (hardware I2C) |

### What is already proven to work

The hardware was tested with simple sketches and all of this worked:
- LED blink test on D5/D6/D7 (green, yellow, red in sequence)
- Ultrasonic distance printed to Serial Monitor, and LEDs changing by distance (under 20 cm green, 20 to 40 cm yellow, over 40 cm red)
- OLED showing text/numbers using the U8g2 library

So the wiring is correct. **Any failure is a software problem.** Write defensive, simple code.

### What went wrong before (so you avoid it)

An earlier sketch was only a serial command listener. After boot it showed "REFLEX 0" on the OLED and then did nothing, because LEDs only changed on `LED x` commands and the ultrasonic only ran after a `STIM` command. The user thought it was broken. **The fix is the DEMO mode below, which is on by default.**

---

## 3. Toolchain

- Board: `arduino:avr:uno`
- Library: **U8g2** by olikraus (only external library allowed). Also uses built-in `Wire.h`.
- Serial: **115200 baud**, 8N1.
- Compile with arduino-cli if available:

```bash
arduino-cli core update-index
arduino-cli core install arduino:avr
arduino-cli lib install "U8g2"
arduino-cli compile --fqbn arduino:avr:uno reflex_fw
```

(The user previously hit "Platform arduino:avr not found". If compiling fails for that reason, run the core install above.)

**Constraints**
- No `String` class (heap fragmentation). Use `char` buffers.
- Use `F("...")` for string literals sent to Serial/OLED to save RAM.
- No `delay()` longer than a few ms in `loop()`. Use `millis()` timing.
- No floating point in `sscanf`. No `Serial.parseInt()`, since it blocks.
- Keep SRAM use under about 60 percent. Print the compile memory report summary in your final message.
- Use the U8g2 **page buffer** (`_1_` constructors) to save RAM, not the full-buffer `_F_` constructors.

---

## 4. OLED configuration (must be easy to switch)

At the top of the sketch, provide a single selector:

```cpp
// 1 = SSD1306 128x64 (default), 2 = SH1106 128x64, 3 = SSD1306 128x32
#define OLED_TYPE 1
// If the screen stays blank, try 0x3D instead of 0x3C
#define OLED_I2C_ADDR 0x3C
```

Constructors to use (page buffer, hardware I2C):

```cpp
#if OLED_TYPE == 1
  U8G2_SSD1306_128X64_NONAME_1_HW_I2C u8g2(U8G2_R0, U8X8_PIN_NONE);
#elif OLED_TYPE == 2
  U8G2_SH1106_128X64_NONAME_1_HW_I2C u8g2(U8G2_R0, U8X8_PIN_NONE);
#elif OLED_TYPE == 3
  U8G2_SSD1306_128X32_UNIVISION_1_HW_I2C u8g2(U8G2_R0, U8X8_PIN_NONE);
#endif
```

After `u8g2.begin()`, call `u8g2.setI2CAddress(OLED_I2C_ADDR * 2);` (U8g2 uses the 8 bit address), then `u8g2.setBusClock(400000);`.

**I2C scan at boot:** before `u8g2.begin()`, scan addresses 0x08 to 0x77 with `Wire`, and print each found address to Serial as `I2C 0x3C`. If none is found print `I2C NONE`. This helps debug a blank screen.

---

## 5. Behaviors

### 5.1 Boot sequence
1. `Serial.begin(115200)`, configure pins (TRIG/LEDs as OUTPUT, ECHO as INPUT).
2. Run the I2C scan, print results.
3. Init OLED.
4. Self test (about 1 second total, using `millis()` or short blocking delays only here in `setup()`): light green, yellow, red for about 250 ms each while the OLED shows `BOOT OK` (for 128x64: large text; for 128x32: small).
5. All LEDs off. Set `demo = true`. Print `READY` then `VER reflex-fw 1.0`.

### 5.2 DEMO mode (default ON after boot)

Purpose: prove the hardware works with no laptop.

Every **150 ms**:
1. Read distance in cm (see 5.4).
2. Set LEDs by zone:
   - no echo: all LEDs off
   - under 20 cm: green only
   - 20 to under 40 cm: yellow only
   - 40 cm or more: red only
3. Redraw the OLED (see layout 6.1) **only if** the displayed integer cm or zone changed, to limit blocking I2C redraws.
4. Print one line to Serial: `DEMO <cm>` (integer cm), or `DEMO -1` if no echo.

Demo mode turns **OFF** automatically when any of these commands arrives: `STIM`, `LED`, `SHOW`. It turns ON/OFF with `DEMO 1` / `DEMO 0`. `PING`, `VER`, `GATE`, `DIST`, `DISARM` do not change demo mode. When demo turns off, turn all LEDs off and print nothing extra (just the command's normal reply).

### 5.3 LIVE mode (laptop controlling)

The laptop sends commands. The Arduino is passive except for reporting a gate hit.

**Armed flow (one rep):**
1. Laptop sends `STIM`. Firmware records `stimUs = micros()` **immediately** on receiving the line (do this before anything else in the handler), sets `armed = true`, `hitSent = false`, replies `OK`.
2. While armed, every **25 ms**, read distance. **Do not redraw the OLED while armed** (redraw blocks about 30 to 40 ms and would wreck timing).
3. The first valid reading with `2 <= cm < gateCm` triggers a hit: compute `dtMs = (micros() - stimUs) / 1000UL`, send `HIT <dtMs> <cm>` once, set `hitSent = true`. Stay armed (so the laptop can read `DIST`), but do not send another `HIT` until the next `STIM`.
4. `DISARM` sets `armed = false`. `SHOW` also sets `armed = false`.
5. Safety timeout: if armed for more than **5000 ms** with no hit, disarm and send `TIMEOUT`.

Use `unsigned long` math for `micros()` differences so wraparound is handled.

### 5.4 Distance measurement

```cpp
// Returns distance in cm, or -1.0 if no valid echo.
float readCm() {
  digitalWrite(TRIG, LOW);  delayMicroseconds(2);
  digitalWrite(TRIG, HIGH); delayMicroseconds(10);
  digitalWrite(TRIG, LOW);
  unsigned long d = pulseIn(ECHO, HIGH, 12000UL); // 12 ms timeout, about 2 m
  if (d == 0) return -1.0;
  float cm = d * 0.01715f;
  if (cm < 2.0f || cm > 200.0f) return -1.0;
  return cm;
}
```

Notes: the sensor needs at least about 25 ms between pings (hence the 25 ms armed interval). Never ping faster than that.

### 5.5 LED rules
- `setLed(char c)`: `'G'` green only, `'Y'` yellow only, `'R'` red only, `'F'` red only (false start), anything else (e.g. `'0'`) all off. Exactly one LED at a time, never more.

---

## 6. OLED screens

Fonts (U8g2, all built in): `u8g2_font_6x10_tr` (small), `u8g2_font_logisoso24_tn` (big digits only, 128x64), `u8g2_font_logisoso16_tn` (big digits for 128x32), `u8g2_font_ncenB10_tr` (medium text).

Use the page loop: `u8g2.firstPage(); do { ... } while (u8g2.nextPage());`

### 6.1 Demo screen (128x64)

- Top-left (0,10): `REFLEX` (small font)
- Top-right (80,10): `DEMO` (small font)
- Center-left (0,50): the distance as a big integer, e.g. `27`. If no echo, draw `--` using the small font medium size instead.
- Next to the number (80,50): `cm` (small font)
- Bottom (0,63): zone text, small font: `CLOSE`, `MID`, `FAR`, or `NO ECHO`

For 128x32: drop the top row, put the big number (16 px font) at (0,26), `cm` next to it, zone text at (70,26).

### 6.2 Result screen (shown after `SHOW`, 128x64)

- Top-left (0,10): `REFLEX`
- Top-right (60,10): `BEST <bestMs>`
- Center-left (0,50): big reaction ms integer
- (88,50): `ms` small
- Grade letter at (108,63) small: `G`, `Y`, `R`, or `F` (F means false start)

### 6.3 Idle screen
Same as the result screen with 0 and `-` until a `SHOW` arrives. Shown only when demo is off and no result exists yet.

---

## 7. Serial protocol (final, authoritative)

- Baud 115200. ASCII. Each message is one line ending with `\n` (accept `\r\n` too).
- Case sensitive, upper case.
- Max line length 47 characters. Longer lines are discarded and answered with `ERR LONG`.
- Parse with a non-blocking line buffer filled from `Serial.read()` inside `loop()`.

### PC to Arduino

| Command | Action | Reply |
|---|---|---|
| `PING` | nothing | `PONG` |
| `VER` | nothing | `VER reflex-fw 1.0` |
| `DEMO 1` / `DEMO 0` | demo mode on/off (off turns LEDs off) | `OK` |
| `GATE <cm>` | set gate distance, clamp to 5 to 200, default 30 | `OK` |
| `DIST` | one reading right now | `DIST <cm>` (integer) or `DIST -1` |
| `STIM` | arm and start the timer (turns demo off) | `OK` |
| `DISARM` | stop watching the ultrasonic | `OK` |
| `LED G` / `LED Y` / `LED R` / `LED 0` | set the LED (turns demo off) | `OK` |
| `SHOW <ms> <grade> <best>` | draw the result screen, disarm (turns demo off). `grade` is one char `G`, `Y`, `R`, or `F`. Example: `SHOW 243 G 210` | `OK` |
| anything else | nothing | `ERR UNKNOWN` |

If a command has bad arguments reply `ERR ARGS`.

### Arduino to PC (unsolicited)

| Message | When |
|---|---|
| `READY` | after boot (note: the Uno resets every time a serial port is opened) |
| `I2C 0x3C` / `I2C NONE` | at boot |
| `DEMO <cm>` | every 150 ms while demo mode is on |
| `HIT <ms> <cm>` | gate tripped while armed. `<ms>` is milliseconds since `STIM` |
| `TIMEOUT` | armed 5 s with no hit |

---

## 8. Acceptance tests (the firmware must pass all of these)

Serial Monitor at 115200 baud with line ending set to **Newline**.

1. **Boot:** the LEDs flash green, yellow, red once, the OLED shows `BOOT OK`, then Serial prints `I2C 0x3C` (or the found address), `READY`, `VER reflex-fw 1.0`.
2. **Demo:** with no commands, `DEMO <cm>` lines stream about 6 or 7 per second. A hand at 10 cm gives green, 30 cm yellow, 60 cm red, nothing in front gives all off and `DEMO -1`. The OLED number follows the distance.
3. **Ping:** `PING` gives `PONG`.
4. **LEDs:** `LED G`, `LED Y`, `LED R` each light only that LED and reply `OK`. `LED 0` turns all off. After the first of these, the `DEMO` lines stop.
5. **Show:** `SHOW 243 G 210` puts `243`, `ms`, `G`, and `BEST 210` on the OLED and replies `OK`.
6. **Gate:** `GATE 30` then `STIM`, then put a hand within 30 cm. Exactly one `HIT <ms> <cm>` line appears, with `<cm>` less than 30. No second `HIT` until the next `STIM`.
7. **Timeout:** `STIM` with nothing near the sensor gives `TIMEOUT` after 5 seconds.
8. **Dist:** `DIST` returns one reading.
9. **Demo re-enable:** `DEMO 1` makes `DEMO <cm>` lines start again.
10. **Bad input:** `HELLO` gives `ERR UNKNOWN`. `GATE` with no number gives `ERR ARGS`.
11. **Stability:** run 5 minutes of demo mode with no resets, no freezes, no garbled serial.

---

## 9. Deliverables

1. `reflex_fw/reflex_fw.ino`: the single sketch. Well commented. Section headers for: config, pins, state, helpers, OLED drawing, command handling, setup, loop.
2. `test_rig.py`: a Python 3 script using `pyserial` that:
   - takes the serial port as a command line argument (e.g. `COM3` or `/dev/ttyACM0`),
   - waits 2 seconds after opening (the Uno resets),
   - runs checks 3 to 10 from section 8 automatically (sending commands, checking replies with timeouts), printing PASS or FAIL for each,
   - for the `HIT` test, prints "Put your hand within the gate distance now" and waits up to 8 seconds.
3. A short final message that includes: the compile memory usage (flash and SRAM), which OLED type and address the code defaults to, and a 3 line "if the screen is blank" checklist (scan output, try `OLED_TYPE` 2, try address 0x3D).

---

## 10. Things NOT to do

- Do not add an encoder, buzzer, or any button code.
- Do not use `String`, `delay()` in `loop()`, or `Serial.parseInt()`.
- Do not use the full-buffer U8g2 constructors (RAM).
- Do not redraw the OLED while armed.
- Do not ping the ultrasonic faster than every 25 ms.
- Do not change the pin map.
- Do not make the demo mode optional or off by default.
