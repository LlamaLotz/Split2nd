# Reflex Trainer: Master Spec (AI Handoff File)

> **If you are an AI assistant reading this file:** this is the complete context for a hackathon project. Treat the pin map, protocol, and part list below as the source of truth. Do not invent pins or parts. If a part differs from what is listed, ask for the exact module name before continuing. When asked to walk through the hardware, go ONE step at a time, wait for the user to confirm each step, and explain what each wire does. The user (hardware lead) is comfortable with electronics but is working fast under a 6 hour deadline, so keep answers short and practical unless asked for depth.

---

## 0. Quick facts

| Item | Value |
|---|---|
| Event | OnHacks (no theme, in person) |
| Time left | about 6 hours |
| Team | 3 people: 2 strong coders (vision/ML, web), 1 hardware lead |
| Concept | Multi-sport reflex trainer: goalies, boxers, F1 / go-kart drivers |
| Core idea | Stimulus appears, athlete reacts, system measures reaction time + decision correctness, gives instant green/yellow/red feedback |
| Hardware | Arduino Uno R3, ultrasonic sensor, LEDs + resistors, breadboard, rotary encoder, 0.9" OLED |
| Software | Python on a laptop (webcam + pose model), web app UI, Arduino firmware |
| Status of judging criteria | NOT verified. Could not retrieve the OnHacks page. Team must paste the real criteria (see section 13) |

---

## 1. The pitch in one paragraph

Elite reflex training normally needs expensive light-board systems (think reaction wall products). We built a low-cost reflex trainer from a webcam, a pretrained pose model, a small model we trained ourselves, and a $25 microcontroller kit. The webcam measures direction and timing of the athlete's movement (X/Y), an ultrasonic sensor adds a hardware-timed depth channel (Z), and a physical LED/OLED unit gives instant feedback. One engine powers three sport modes: **Goalie** (directional save drills), **Boxing** (slip / block / counter drills), and **Racing** (F1 lights-out start and brake/steer cues).

---

## 2. Honest design decisions (read this, it shapes everything)

1. **The Arduino does NOT run AI.** An Uno has 2 KB RAM. Vision and ML run on the laptop. The Arduino is the physical I/O unit: ultrasonic, encoder, LEDs, OLED.
2. **We use a pretrained pose model** (MediaPipe) for body tracking. We do not train that from scratch.
3. **We DO train one small model ourselves** (section 8): a direction/response classifier on data we collect at the hackathon. That is the honest "we trained a model" claim. It is a scikit-learn model on pose features, not a deep net.
4. **Always build the rule-based version first.** A velocity-threshold direction detector may match the ML model. Show both and report accuracy. Judges respect an honest comparison more than a fake AI claim.
5. **Reaction time from a webcam is approximate.** Frame quantization is about 33 ms at 30 fps (about 16 ms at 60 fps), plus camera/USB/processing latency. Treat webcam RT as "good to roughly +/- 50 ms, consistent between reps". The ultrasonic channel is timed on the Arduino in microseconds, so it is the precise cross-check for the "reach/step into zone" event.
6. **Pitch the limitation yourselves.** Saying "our measurement noise is about X ms, measured over 30 repeat reps" makes you look rigorous.
7. **Scope discipline:** one engine, three skins. Do not build three separate products.

---

## 3. Reaction science basics (for defaults and for the pitch)

- Simple visual reaction time (one stimulus, one response) for most people is about 200 to 280 ms.
- Choice reaction time (stimulus tells you WHICH response) is slower, typically adding 100 to 200 ms. More choices = slower (Hick's law).
- Responses under about 100 ms after the stimulus are treated as anticipation / false start (athletics rules use 100 ms). We flag these instead of rewarding them.
- Defaults below are **starting points to tune on teammates**, not scientific norms.

| Drill type | Green | Yellow | Red |
|---|---|---|---|
| Simple (F1 lights-out) | <= 250 ms | 251 to 350 ms | > 350 ms |
| Choice (goalie, boxing) | <= 380 ms | 381 to 500 ms | > 500 ms |
| Wrong direction | always Red | | |
| < 100 ms | "False start" (purple on screen, Red LED) | | |

Adaptive mode (stretch): after 10 reps, set thresholds from the athlete's own median (green = faster than own median, yellow = within +20%, red = worse).

---

## 4. System architecture

```
                 +-----------------------------+
 Webcam -------->|  Laptop (Python backend)    |<------ Browser (web UI)
 (pose, X/Y)     |  - capture + MediaPipe      |  WS    - mode select
                 |  - onset + direction logic  |        - stimulus display
                 |  - ML classifier (sklearn)  |        - live video + skeleton
                 |  - drill state machine      |        - results, history
                 |  - FastAPI + WebSocket      |
                 +--------------+--------------+
                                | USB serial, 115200 baud
                         +------v------+
                         | Arduino Uno |
                         +--+--+--+--+-+
        Ultrasonic (Z) -----+  |  |  +---- OLED (I2C)
        Rotary encoder -------+  |  +------ LEDs G/Y/R
```

**Data flow for one rep:**
1. Backend picks a random wait (1.5 to 4 s), then shows the stimulus in the browser.
2. At that moment backend records `t_stim = time.perf_counter()` and sends `STIM` to the Arduino (arms the ultrasonic timer).
3. Webcam frames (each timestamped at capture) feed pose tracking. Onset detector finds when movement started. Classifier says which direction.
4. If the ultrasonic gate trips, Arduino sends `HIT <ms>`.
5. Backend computes RT, checks direction, grades G/Y/R, sends `LED` and `SHOW` to the Arduino, and a result to the browser.

---

## 5. Hardware

### 5.1 Parts (confirm exact modules)

| Part | Expected module | Notes |
|---|---|---|
| Arduino Uno R3 | Uno R3 | 5 V logic, USB serial |
| Ultrasonic | HC-SR04 (4 pin: VCC, TRIG, ECHO, GND) | 5 V. Range about 2 cm to 4 m, 15 degree cone |
| LEDs | 1 green, 1 yellow, 1 red (confirm colors!) | Need a resistor for each |
| Resistors | 220 ohm (330 ohm also fine) | One per LED |
| Breadboard | any | Use power rails for 5V and GND |
| Rotary encoder | KY-040 style (CLK, DT, SW, +, GND) | Has push button |
| OLED | SSD1306 I2C. "0.9 inch" is probably 0.91" 128x32 or 0.96" 128x64 | Check the resolution, it changes one line of code |

### 5.2 Pin map (SOURCE OF TRUTH)

| Function | Arduino pin | Notes |
|---|---|---|
| Ultrasonic TRIG | D9 | |
| Ultrasonic ECHO | D10 | |
| Encoder CLK | D2 | Interrupt pin (INT0) |
| Encoder DT | D3 | Interrupt pin (INT1) |
| Encoder SW (button) | D4 | INPUT_PULLUP, pressed = LOW |
| LED Green | D5 | via 220 ohm to GND |
| LED Yellow | D6 | via 220 ohm to GND |
| LED Red | D7 | via 220 ohm to GND |
| OLED SDA | A4 | Hardware I2C |
| OLED SCL | A5 | Hardware I2C |
| 5V rail | 5V | to HC-SR04 VCC, encoder +, OLED VCC |
| GND rail | GND | all grounds common |

### 5.3 Wiring steps (do in this order, power OFF while wiring)

1. **Power rails:** Uno 5V to breadboard + rail. Uno GND to breadboard - rail.
2. **LEDs:** for each LED, long leg (anode) to a resistor, resistor to the Uno pin (D5/D6/D7). Short leg (cathode) to GND rail.
3. **Ultrasonic:** VCC to + rail, GND to - rail, TRIG to D9, ECHO to D10.
4. **Encoder:** + to + rail, GND to - rail, CLK to D2, DT to D3, SW to D4.
5. **OLED:** GND to - rail, VCC to + rail (most 4 pin modules have a regulator and accept 5 V; if the board says 3.3 V only, use the Uno 3.3V pin), SDA to A4, SCL to A5.
6. Visual check: no bare legs touching, every GND shared.
7. Plug in USB. Upload the test sketches in order (section 5.5).

### 5.3b Electrical sanity

- LED current about 9 to 14 mA each through 220 ohm at 5 V, safely under the pin limit.
- HC-SR04 draws about 15 mA. All fine on USB power.
- Echo output is 5 V into a 5 V Uno, no level shifting needed.

### 5.4 Physical rig layout

- Laptop screen is the main stimulus display. Athlete stands or sits about 0.8 to 1.5 m away depending on mode.
- Webcam: chest height, facing the athlete, full upper body in frame, good front lighting (low light drops the frame rate because of auto-exposure). Set camera to 640x480, MJPG, 30 or 60 fps.
- Ultrasonic: aimed at the athlete's reach/response zone, about 40 to 80 cm away, mounted stable (tape it to a box or a tripod). Hard flat surfaces (hand, torso) reflect best. Soft sleeves can read poorly.
- LED + OLED unit: on a small stand at eye line next to the screen so the athlete sees the green/yellow/red without looking away.
- Floor tape marks for foot position so every athlete stands in the same spot.
- Keep a clear area. Athletes lunge. Nothing breakable nearby.

### 5.5 Firmware bring-up tests (do these before any integration)

1. **Blink test:** cycle D5/D6/D7. Confirms LEDs, resistors, orientation.
2. **Ultrasonic test:** print cm to Serial Monitor. Wave a hand at 20, 40, 60 cm and check values.
3. **Encoder test:** print +1/-1 and button presses. If direction is backwards, swap the sign in code (or swap CLK/DT wires).
4. **OLED test:** run an I2C scanner (expect address 0x3C, sometimes 0x3D), then draw text.
5. **Close the Arduino IDE Serial Monitor** before Python opens the port (only one program can hold it).

---

## 6. Firmware (Arduino Uno)

Library: **U8g2** (works with both 128x64 and 128x32 OLEDs). Uses the page-buffer constructor to save RAM.

```cpp
#include <Arduino.h>
#include <Wire.h>
#include <U8g2lib.h>

// ---------- Pins ----------
const uint8_t PIN_TRIG = 9, PIN_ECHO = 10;
const uint8_t PIN_ENC_CLK = 2, PIN_ENC_DT = 3, PIN_ENC_SW = 4;
const uint8_t PIN_LED_G = 5, PIN_LED_Y = 6, PIN_LED_R = 7;

// ---------- OLED: pick ONE ----------
U8G2_SSD1306_128X64_NONAME_1_HW_I2C u8g2(U8G2_R0, U8X8_PIN_NONE);   // 0.96" 128x64
// U8G2_SSD1306_128X32_UNIVISION_1_HW_I2C u8g2(U8G2_R0, U8X8_PIN_NONE); // 0.91" 128x32

// ---------- State ----------
volatile int8_t encDelta = 0;
bool armed = false, hitSent = false;
unsigned long stimUs = 0;
int gateCm = 30;                 // trigger when object is closer than this
unsigned long lastPingMs = 0, lastBtnMs = 0;
bool lastBtn = HIGH;
char lineBuf[48];
uint8_t lineLen = 0;

// Last result shown on OLED
unsigned int shownMs = 0, bestMs = 0;
char shownGrade = '-';

void onEncClk() {                // FALLING edge on CLK
  static unsigned long last = 0;
  unsigned long now = micros();
  if (now - last < 1500) return; // debounce
  last = now;
  encDelta += (digitalRead(PIN_ENC_DT) == HIGH) ? 1 : -1; // flip sign if reversed
}

float readCm() {
  digitalWrite(PIN_TRIG, LOW);  delayMicroseconds(2);
  digitalWrite(PIN_TRIG, HIGH); delayMicroseconds(10);
  digitalWrite(PIN_TRIG, LOW);
  unsigned long d = pulseIn(PIN_ECHO, HIGH, 12000UL); // 12 ms timeout, about 2 m
  if (d == 0) return -1;
  return d * 0.01715f;           // microseconds to cm (round trip)
}

void setLed(char c) {
  digitalWrite(PIN_LED_G, c == 'G');
  digitalWrite(PIN_LED_Y, c == 'Y');
  digitalWrite(PIN_LED_R, c == 'R');   // any other char = all off
}

void drawScreen() {              // blocking (about 30 to 40 ms), call only when NOT armed
  u8g2.firstPage();
  do {
    u8g2.setFont(u8g2_font_6x10_tr);
    u8g2.setCursor(0, 10);  u8g2.print(F("REFLEX"));
    u8g2.setCursor(80, 10); u8g2.print(F("BEST "));
    u8g2.print(bestMs);
    u8g2.setFont(u8g2_font_logisoso24_tn);
    u8g2.setCursor(0, 50);  u8g2.print(shownMs);
    u8g2.setFont(u8g2_font_6x10_tr);
    u8g2.setCursor(100, 50); u8g2.print(shownGrade);
  } while (u8g2.nextPage());
}

void handleLine(char *s) {
  if (!strcmp(s, "PING"))            Serial.println(F("PONG"));
  else if (!strncmp(s, "GATE ", 5))  gateCm = atoi(s + 5);
  else if (!strcmp(s, "STIM"))       { stimUs = micros(); armed = true; hitSent = false; }
  else if (!strcmp(s, "DISARM"))     armed = false;
  else if (!strncmp(s, "LED ", 4))   setLed(s[4]);
  else if (!strncmp(s, "SHOW ", 5)) {            // SHOW <ms> <G|Y|R|F> <bestMs>
    unsigned int ms, best; char g;
    if (sscanf(s + 5, "%u %c %u", &ms, &g, &best) == 3) {
      shownMs = ms; shownGrade = g; bestMs = best;
      armed = false;
      drawScreen();
    }
  }
}

void setup() {
  pinMode(PIN_TRIG, OUTPUT); pinMode(PIN_ECHO, INPUT);
  pinMode(PIN_ENC_CLK, INPUT_PULLUP); pinMode(PIN_ENC_DT, INPUT_PULLUP);
  pinMode(PIN_ENC_SW, INPUT_PULLUP);
  pinMode(PIN_LED_G, OUTPUT); pinMode(PIN_LED_Y, OUTPUT); pinMode(PIN_LED_R, OUTPUT);
  attachInterrupt(digitalPinToInterrupt(PIN_ENC_CLK), onEncClk, FALLING);
  Serial.begin(115200);
  u8g2.begin(); u8g2.setBusClock(400000);
  drawScreen();
  Serial.println(F("READY"));
}

void loop() {
  // 1) serial lines from laptop
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n' || c == '\r') {
      if (lineLen) { lineBuf[lineLen] = 0; handleLine(lineBuf); lineLen = 0; }
    } else if (lineLen < sizeof(lineBuf) - 1) lineBuf[lineLen++] = c;
  }

  // 2) encoder + button events
  int8_t d; noInterrupts(); d = encDelta; encDelta = 0; interrupts();
  if (d) { Serial.print(F("ENC ")); Serial.println(d); }
  bool b = digitalRead(PIN_ENC_SW);
  if (b != lastBtn && millis() - lastBtnMs > 30) {
    lastBtnMs = millis(); lastBtn = b;
    if (b == LOW) Serial.println(F("BTN"));
  }

  // 3) ultrasonic every 25 ms while armed (about 40 Hz)
  if (armed && millis() - lastPingMs >= 25) {
    lastPingMs = millis();
    float cm = readCm();
    if (!hitSent && cm > 0 && cm < gateCm) {
      hitSent = true;
      unsigned long dtUs = micros() - stimUs;   // includes up to one ping duration
      Serial.print(F("HIT ")); Serial.print(dtUs / 1000UL);
      Serial.print(' ');       Serial.println((int)cm);
    }
  }
}
```

**Firmware notes**
- The OLED redraw blocks about 30 to 40 ms, so it is only done when the system is not armed (after a result). Never redraw during a rep.
- Ultrasonic resolution is limited by the 25 ms ping interval. Document it as "about +/- 25 ms on the Z channel". You can drop to about 15 ms if echoes are clean, but below about 10 ms you get echo crosstalk.
- If the OLED stays blank, run an I2C scanner and check the constructor matches the real resolution.

### 6.1 Serial protocol (115200 baud, newline terminated, ASCII)

| Direction | Message | Meaning |
|---|---|---|
| PC to Arduino | `PING` | Arduino replies `PONG` |
| PC to Arduino | `GATE <cm>` | Set ultrasonic trigger distance |
| PC to Arduino | `STIM` | Start timer, arm ultrasonic |
| PC to Arduino | `DISARM` | Stop watching ultrasonic |
| PC to Arduino | `LED G` / `LED Y` / `LED R` / `LED 0` | Set feedback LED (0 = all off) |
| PC to Arduino | `SHOW <ms> <G/Y/R/F> <bestMs>` | Draw result on OLED (F = false start) |
| Arduino to PC | `READY` | Boot finished |
| Arduino to PC | `ENC <+1/-1>` | Encoder turned (use to switch mode or difficulty) |
| Arduino to PC | `BTN` | Encoder button pressed (start / confirm) |
| Arduino to PC | `HIT <ms> <cm>` | Ultrasonic gate tripped, ms since `STIM` |

Encoder usage: turn = cycle mode (Goalie, Boxing, Racing) or difficulty; press = start rep / confirm. This makes the physical unit a real controller, not just decoration.

---

## 7. Software (laptop)

### 7.1 Stack

- Python 3.10+, `opencv-python`, `mediapipe`, `numpy`, `pyserial`, `scikit-learn`, `joblib`, `fastapi`, `uvicorn`
- Frontend: plain HTML + JS (or React/Vite if the web coder prefers) talking to FastAPI over WebSocket
- Storage: SQLite or a JSON file for session history (no cloud needed)

### 7.2 Modules (suggested file layout)

```
reflex/
  backend/
    main.py          # FastAPI app, websocket, serves frontend
    rig.py           # serial bridge to Arduino
    vision.py        # camera thread + MediaPipe + speed signals
    onset.py         # reaction onset detection
    classifier.py    # feature extraction + sklearn model
    drills.py        # state machine + sport modes
    scoring.py       # grading, adaptive thresholds
    store.py         # sessions, history
  frontend/
    index.html, app.js, style.css
  data/              # collected reps (CSV) + trained model.joblib
  firmware/
    reflex_rig.ino
```

### 7.3 Serial bridge (rig.py)

```python
import serial, threading, queue, time

class Rig:
    def __init__(self, port, baud=115200):
        self.ser = serial.Serial(port, baud, timeout=0.05)
        time.sleep(2)                       # Uno resets when the port opens
        self.events = queue.Queue()
        threading.Thread(target=self._rx, daemon=True).start()

    def send(self, line: str):
        self.ser.write((line + "\n").encode())

    def _rx(self):
        while True:
            raw = self.ser.readline()
            if raw:
                self.events.put((time.perf_counter(),
                                 raw.decode(errors="ignore").strip()))
```

### 7.4 Vision signals (vision.py)

- Capture thread: `cap.read()` then immediately `t = time.perf_counter()` and store `(t, frame)`. Timestamp at capture, not after processing.
- Run MediaPipe Pose (lite model) on each frame. Use landmarks: nose, shoulders, wrists, hips.
- Normalize by shoulder width so distance from the camera does not matter. Coordinates in shoulder-widths.
- Per-frame **motion speed** = frame-to-frame displacement of a chosen landmark set, divided by dt, lightly smoothed (EMA about 0.5).
- Which landmarks per mode: Goalie = hip center + wrists; Boxing = nose + wrists + shoulders; Racing = wrists (hand press/lift) or right foot if visible.
- Draw the skeleton on the preview. This looks great in the demo.

### 7.5 Onset detection (onset.py)

```python
import numpy as np

def detect_onset(samples, t_stim, k=4.0, floor=0.3, need=2):
    """samples: list of (t, speed) with speed in shoulder-widths/second.
    Returns time movement started after t_stim, or None."""
    base = [s for t, s in samples if t_stim - 0.4 <= t < t_stim]
    if len(base) < 5:
        return None
    thr = max(np.mean(base) + k * np.std(base), floor)
    run, first_t = 0, None
    for t, s in samples:
        if t < t_stim:
            continue
        if s > thr:
            if run == 0:
                first_t = t
            run += 1
            if run >= need:
                return first_t
        else:
            run, first_t = 0, None
    return None

# reaction time (ms) = (onset_t - t_stim) * 1000
```

Notes: the athlete must be still for the 0.4 s before the stimulus (the drill state machine enforces a "hold still" check and restarts the wait if they fidget). Tune `k` and `floor` on teammates.

### 7.6 Drill state machine (drills.py)

```
IDLE -> ARM (hold-still check) -> WAIT (random 1.5 to 4.0 s)
     -> STIM (t_stim, send STIM) -> RESPONSE WINDOW (1.2 s)
     -> RESULT (grade, LED, OLED, UI) -> IDLE
```

Rules:
- Movement during WAIT = false start (counts, red/purple).
- RT < 100 ms = anticipation, flagged.
- No response in 1.2 s = miss.
- Direction mismatch (choice drills) = wrong, always Red.

### 7.7 Scoring (scoring.py)

```python
DEFAULTS = {"simple": (250, 350), "choice": (380, 500)}

def grade(rt_ms, kind="simple", correct=True):
    if not correct:
        return "R"
    g, y = DEFAULTS[kind]
    return "G" if rt_ms <= g else "Y" if rt_ms <= y else "R"
```

Session stats to show: last RT, best, median, mean, std (consistency), accuracy %, G/Y/R counts, trend line, and a "fatigue" flag if the last 5 reps are slower than the first 5 by more than 15%.

---

## 8. The model we train (the honest version)

### 8.1 What it does

A **response direction / type classifier**. Input: pose features from the 0.6 s window after the stimulus. Output: what the athlete actually did (LEFT, RIGHT, UP, DOWN, NONE, plus sport specific labels like SLIP_L, SLIP_R, DUCK, BLOCK, PUNCH). This lets us measure *decision accuracy*, not just speed, which is the sports-relevant part.

### 8.2 Why this is a real use of ML

- Different sports produce different movement shapes; a hand-tuned rule per move gets brittle.
- Once trained, the classifier generalizes across athletes better than thresholds tuned on one person.
- But: **build the rule-based detector first** (net displacement of landmarks). If the model does not beat it, say so. Report both.

### 8.3 Data collection (about 45 to 60 min of the 6 hours)

- Add a **Training Mode** to the web app: screen says "MOVE LEFT", athlete moves, system records the rep. **Label = the instruction**, so labeling is automatic.
- Target: 40 reps per class per mode x 3 to 5 people. About 400 to 800 reps total is plenty for a small model.
- Include NONE reps (stay still) so the model learns "no response".
- Vary people, clothing, and distance a bit so it does not overfit to one teammate.
- Save one row per rep: athlete id, mode, label, features, raw landmark window (CSV or npy).

### 8.4 Features

For landmarks (nose, L/R shoulder, L/R wrist, hip center), in shoulder-width units, relative to the pre-stimulus rest position:
- net dx, dy over 0.1 to 0.6 s
- peak speed and time to peak
- mean dx, dy per 100 ms bin (6 bins)
- left/right asymmetry of wrist motion
- ultrasonic distance change (if gate hit)

### 8.5 Model and training

```python
import numpy as np, joblib
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split, cross_val_score
from sklearn.metrics import classification_report, confusion_matrix

X, y = np.load("data/X.npy"), np.load("data/y.npy", allow_pickle=True)
# IMPORTANT: split by PERSON, not by rep, or accuracy will look fake.
Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.25, stratify=y, random_state=0)

clf = RandomForestClassifier(n_estimators=200, random_state=0)
clf.fit(Xtr, ytr)
print(classification_report(yte, clf.predict(Xte)))
print(confusion_matrix(yte, clf.predict(Xte)))
joblib.dump(clf, "data/model.joblib")
```

Better evaluation: leave-one-person-out (train on 3 teammates, test on the 4th). That number is the honest one to quote.

### 8.6 Pitch claim (safe wording)

"Pretrained MediaPipe for pose. We trained our own response classifier on N reps from M athletes and got X% accuracy on a held-out person, compared with Y% for our rule-based baseline."

### 8.7 Fallback

If the model is flaky by hour 4:30, ship the rule-based detector and show the trained model as a "research mode" toggle with its confusion matrix on a slide. Never let a flaky model break the main demo.

### 8.8 Stretch: adaptive coach (cheap and effective)

Per-athlete rolling median + MAD of RT. The UI says things like "faster than your usual", "slowing down, fatigue", or "left side 60 ms slower than right". It is plain statistics, call it an adaptive scoring engine, not deep learning.

---

## 9. The three sport modes (one engine, three skins)

All modes share: drill state machine, onset detector, classifier, scoring, LEDs/OLED, history. Only the stimulus and the expected response change.

### 9.1 Racing mode (build FIRST: simplest, best demo)

- **F1 Lights Out:** five red lights on screen light up one by one, then all go out after a random 0.2 to 3 s hold. Athlete reacts as fast as possible (hand lift/press, or foot if visible).
- Type: simple RT. Grading: simple thresholds. Flags jumped starts.
- **Brake / Steer cue (stretch):** screen shows a red BRAKE flash or an arrow LEFT/RIGHT. Response: hands move on a virtual wheel or lean. Choice RT.
- Ultrasonic: hand crosses the gate above the "throttle" zone.
- Why first: everyone knows the F1 start, it is a clean single-variable test, and it validates the whole pipeline.

### 9.2 Goalie mode

- Stimulus: a ball/target appears in one of 4 to 6 zones (left/right x high/low, optional center) with a quick animation toward the athlete.
- Response: lunge/reach toward that zone. Classifier decides direction. Ultrasonic gate = hand/leg reached the "save plane".
- Metrics: RT, direction accuracy, left vs right bias, zone heat map.
- Natural story hook: the hardware lead is a competitive goalie (field hockey), so this is a built-from-experience tool. Use only if the team likes it.

### 9.3 Boxing mode

- Stimulus: incoming-punch cue (arrow or glove icon from left/right/center).
- Defensive drill: slip left / slip right / duck / block. Head and wrist landmarks classify it.
- Offensive drill: "hit the pad" light cue, athlete throws a jab at a hand pad in front of the ultrasonic sensor; the gate times it precisely.
- Metrics: RT, correct defense %, counter-punch latency.
- Safety: shadow-box only. Nobody punches the laptop or the sensor.

### 9.4 Cross-sport "Reflex Profile" (the unifying feature)

Each athlete gets a profile: median simple RT, median choice RT, accuracy, consistency (std), fatigue slope, left/right bias. Show it as a radar chart. This is what ties the three sports together in the pitch.

---

## 10. Web app

### 10.1 Pages / sections

1. **Home / Mode picker:** three big cards (Racing, Goalie, Boxing) + Profile. Also switchable by the physical encoder.
2. **Drill screen:** full-width stimulus panel, live webcam with skeleton overlay in a corner, big reaction time readout, G/Y/R banner mirroring the LEDs, rep counter.
3. **Results:** per-session summary, RT histogram, trend line, accuracy, bias.
4. **Profile:** radar chart across drills, personal best, history.
5. **Training Mode (admin):** data collection UI for the classifier.
6. **About / How it works:** architecture diagram, measurement limits, team. (Judges read this.)

### 10.2 Backend endpoints

- `GET /` static frontend
- `GET /video` MJPEG stream of annotated frames
- `WS /ws` pushes state: `{phase, mode, stimulus, rt_ms, grade, correct, stats}` and accepts `{cmd: start|stop|mode|difficulty}`
- `GET /history` past sessions
- `POST /training/start` data collection

### 10.3 UI rules

- High contrast, huge text, readable from 1.5 m.
- The stimulus must appear in one frame with no animation lag. Do NOT use CSS transitions on the stimulus element (they add latency and jitter). Toggle a class/color instantly.
- Keep the page on a fixed 60 Hz display, avoid power-saving modes, close other heavy tabs.
- Display refresh adds roughly 8 to 16 ms of unmeasured latency. Mention it in "measurement limits".

---

## 11. Measurement validity (slide-worthy)

Do this in 15 minutes; it makes the project look rigorous.

1. **Repeatability:** one teammate does 30 simple-RT reps. Report mean and std. Std of about 30 to 50 ms is normal for humans.
2. **Cross-sensor agreement:** on reaches, compare webcam onset vs ultrasonic `HIT`. Plot the difference histogram; report its median and spread.
3. **Latency note:** state the webcam fps, camera latency uncertainty, and display lag. Say "relative scores are reliable; absolute values are approximate within about 50 ms".
4. **Threshold tuning:** use team data to justify the G/Y/R cutoffs.

---

## 12. Six hour plan (3 people)

Roles: **A = vision/ML coder**, **B = web/backend coder**, **H = hardware lead**.

| Time | H (hardware) | A (vision/ML) | B (web/backend) |
|---|---|---|---|
| 0:00 to 0:30 | Verify parts, wire breadboard, blink/ultrasonic/encoder/OLED tests | Install mediapipe + opencv, confirm webcam fps | Repo, FastAPI skeleton, WebSocket hello |
| 0:30 to 1:30 | Full firmware + serial protocol; test with a Python script | Pose capture thread, speed signal, onset detector | Mode picker UI, stimulus panel, state machine UI |
| 1:30 to 2:30 | Integrate rig.py into backend; mount sensor/LED/OLED on stand | Hook onset into state machine | **Racing mode end-to-end** (F1 lights, RT, LEDs, OLED) |
| 2:30 to 3:30 | Build stand/rig; calibrate gate distance; start data collection | Training Mode data collection + features | Goalie mode UI + results/history |
| 3:30 to 4:30 | Run data collection with 3+ people | Train classifier, rule-based baseline, evaluation | Boxing mode UI, profile radar chart |
| **4:30** | **FEATURE FREEZE** | | |
| 4:30 to 5:15 | Rig polish, cable management, backup video of the demo | Measurement validity numbers (section 11) | README, About page, final UI polish |
| 5:15 to 6:00 | Pitch rehearsal x3, submission, demo setup | | |

**Priority ladder (cut from the bottom):**
- P0: Racing mode end-to-end with LEDs, OLED, encoder, webcam onset
- P1: Goalie mode with direction detection (rule-based)
- P2: Boxing mode
- P3: Trained classifier + validity slide
- P4: Profile radar, adaptive coach

**Rules:** commit often; keep `main` demo-safe, risky work on branches; record a backup demo video at hour 4:45 in case hardware fails on stage.

---

## 13. Judging and pitch

### 13.1 Criteria status

The OnHacks judging page could not be retrieved. **Paste the real criteria into this section** before using this file with another AI. Typical hackathon rubrics weigh: innovation/creativity, technical implementation, impact/usefulness, and presentation/demo (often 20 to 25% each). Plan for those until confirmed.

### 13.2 Map the project to likely criteria

| Criterion | Our answer |
|---|---|
| Innovation | Multi-sensor fusion (webcam X/Y + ultrasonic Z) low-cost reaction wall; one engine for three sports |
| Technical | Real-time vision, serial firmware, timing methodology, a small trained model with held-out-person evaluation |
| Impact | Cheap alternative to expensive reaction-training systems for youth and amateur athletes, schools, clubs |
| Presentation | Live demo with physical LEDs, OLED, and a fun head-to-head between judges |

### 13.3 Demo script (2 to 3 min)

1. (15 s) Problem: reflexes decide games and races, but training tools cost thousands.
2. (60 s) Live demo: invite a judge to the F1 lights-out. LEDs go green/yellow/red, OLED shows ms. Beat their score.
3. (30 s) Switch to Goalie with the encoder knob; show direction accuracy and the left/right bias.
4. (20 s) Architecture in one slide, and the honest "what we trained" line.
5. (20 s) Validity numbers and limits.
6. (15 s) Where it goes next: more sensors, mobile, coach dashboards.

**Always end the demo with the judge's score on a leaderboard.** Competitive judges remember the project.

---

## 14. Names

Top picks:
- **SplitSecond** (clear, memorable, sports-neutral)
- **Hairtrigger** (reflex + trigger, punchy)
- **ReflexRig** (descriptive, clear hardware identity)

More options: **Twitch**, **Snapback**, **Quickdraw**, **ReactLab**, **Latency**, **Blink**, **ZeroPoint**, **Apex Reflex**, **FlashPoint**, **Synapse Arena**, **Reflexa**, **PitStop**, **Netminder**, **Jab**, **Gate** (like a starting gate), **Reaction Wall**, **Millisecond Club**, **Fastlane**.

Tagline ideas: "Train the first 250 milliseconds." / "Where games are won in a split second." / "Measure your reflexes. Beat them."

Check quickly that the name is free as a domain/GitHub org if you plan to keep the project.

---

## 15. Troubleshooting

| Symptom | Fix |
|---|---|
| Serial port busy | Close Arduino IDE Serial Monitor |
| Arduino resets when Python opens port | Normal; wait 2 s (already in `Rig`) |
| Ultrasonic reads 0 / -1 / jumps | Check TRIG/ECHO not swapped, flat hard target, aim away from table, add a 3 sample median |
| OLED blank | Scan I2C (0x3C vs 0x3D), match constructor to real resolution, check SDA/SCL on A4/A5 |
| Encoder counts backwards | Flip sign in `onEncClk` or swap CLK/DT |
| Encoder skips/double counts | Increase debounce from 1500 us, add 10 nF cap on CLK/DT if available |
| Webcam fps low | Add light, lock exposure, 640x480 MJPG, use MediaPipe lite model |
| Pose jittery | Plain background, contrasting clothes, full upper body in frame |
| False onsets | Increase `k`, enforce hold-still check, lower smoothing |
| UI stimulus feels late | Remove CSS transitions, close other tabs, use fullscreen |
| LEDs very dim | Check polarity (long leg to resistor side), resistor value |

---

## 16. How to use this file with another AI

Paste/upload this file, then say one of:

- "Walk me through the hardware wiring one step at a time. Confirm each step before moving on." (the AI should follow section 5)
- "Debug my ultrasonic sensor, it reads [X]." (section 5.5 and 15)
- "Write the full FastAPI backend for sections 7 and 10."
- "Write the Training Mode and feature extraction code for section 8."
- "Help me build the Goalie mode frontend."
- "Help me write the 3 minute pitch using section 13."

The AI should ask for any missing detail (exact OLED size, LED colors, laptop OS, webcam model) rather than guess.

---

## 17. Open items to confirm

- Exact OLED resolution (128x32 or 128x64) and I2C address
- Do we really have green, yellow, and red LEDs?
- Webcam model and max fps on the demo laptop
- Laptop OS (affects serial port name: `COM3`, `/dev/ttyACM0`, `/dev/cu.usbmodem...`)
- Real OnHacks judging criteria, prizes, demo format (table vs stage), and submission platform
- Space and power at the venue for a standing demo
