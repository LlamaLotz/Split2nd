# Hardware link: Lenovo camera, Arduino Uno, LEDs, and the edge glow

Status: **backend + frontend module complete; validation on the Windows demo
laptop pending.** This document is the contract between the laptop, the Uno,
and the app pages. It covers the current rig only: integrated Lenovo webcam,
Arduino Uno/ultrasonic/LED/OLED on USB, and the laptop screen as the display.
No phone client is involved.

The one-line version: **the app owns the Arduino after `READY`, the physical
LED mirrors the result colour for 900 ms and is then released, and the page
edge glow mirrors the same colour.**

---

## 1. Lens: integrated Lenovo camera

| Setting | Value |
|---|---|
| Camera index | `0` (integrated Lenovo webcam) |
| Backend | `CAP_DSHOW` on Windows |
| Resolution | 640 x 480 |
| Frame rate | 30 fps requested |
| Pixel format | MJPG |
| Capture path | `backend/vision.py` (read-only for this module) |

`scripts/run.py --camera 0` sets `CAMERA_INDEX` before the app imports, so the
capture thread opens the right device. `CAMERA_WIDTH`, `CAMERA_HEIGHT` and
`CAMERA_FPS` are in `backend/config.py`.

`GET /api/health` now carries the configured camera contract without changing
the Pose Landmarker health:

```json
"camera": {
  "index": 0,
  "width": 640,
  "height": 480,
  "fps": 30,
  "backend": "DSHOW",
  "fourcc": "MJPG"
},
"vision": {
  "mode": "local",
  "pose_ok": true,
  "pose_backend": "tasks",
  "pose_model_exists": true
}
```

Keep the camera unmirrored. MediaPipe's left/right labels and the boxing
classifier both rely on the real image orientation.

### Framing (boxing needs more than racing)

Step back until **the head and both hands are in frame with margin**. The
top of the head should not touch the top edge, and both wrists must stay in
frame at the end of a jab, hook or uppercut. Slips need the head to move
sideways without leaving the frame; a block needs both hands visible and high.

Run:

```powershell
.venv\Scripts\python scripts\check_camera.py --seconds 4
```

It prints the negotiated resolution, the measured fps, the frame brightness,
and pose framing warnings. A black frame usually means the ThinkPad privacy
shutter is closed, Windows camera permission is off, or another app (Teams,
Zoom, the Arduino IDE is not the culprit here) is holding the camera. A
missing pose means step back, add light, and use a plain background.

The primary pose path is the **MediaPipe Tasks Pose Landmarker**:
`models/pose_landmarker_lite.task`, `RunningMode.VIDEO`, one pose, 0.5
detection/tracking confidence. A correct block may return `reaction_ms: null`
at a sparse frame rate; that is expected.

---

## 2. Arduino Uno on COMx

### Pre-flight

1. Plug the Uno into the laptop with a **data** USB cable (not charge-only).
2. **Close the Arduino IDE Serial Monitor** and any other serial terminal.
3. Check Device Manager -> **Ports (COM & LPT)**:
   - genuine Uno: `Arduino Uno (COMx)`,
   - clone: `USB-SERIAL CH340 (COMx)`.
4. If the CH340 port appears with a warning, install the WCH CH340/CH341
   driver and replug. The board is 115200 8N1.

One program owns `COMx` at a time. The app, `scripts/check_rig.py`, the
Arduino IDE Serial Monitor, PuTTY and `test_rig.py` are all candidates. A
busy port is reported as a clear status in `/api/health`:

```json
"rig": {
  "port": "COM6",
  "connected": false,
  "port_busy": true,
  "last_error": "COM6 is busy - close the Arduino IDE Serial Monitor and the rig reconnects automatically"
}
```

The serial loop retries every 1.5 s. It does not crash and does not require a
restart after the other program releases the port.

### Bring-up

```powershell
.venv\Scripts\python scripts\check_rig.py --list
.venv\Scripts\python scripts\check_rig.py --port COM6 --seconds 10
.venv\Scripts\python scripts\check_rig.py --port COM6 --seconds 10 --gate 60 --led
```

Expected, in order:

1. The Uno resets when the port opens. `READY` arrives about 1.5-2 s later.
2. The app sends **`DEMO 0`** immediately. The firmware boots in standalone
   demo mode; `DEMO 0` stops the distance-driven LED loop so the app owns the
   unit.
3. The app sends **`PING`** and **`VER`**, then caches:
   - `VER reflex-fw 1.0` as `rig.firmware`,
   - `I2C 0x3C` (or `I2C NONE`) as `rig.i2c`.
4. `DIST <cm>` returns one reading. The app polls it at about **4 Hz only
   while idle**, for gate calibration. It is never polled during arm, wait or
   response.
5. **`GATE 60`** sets a 60 cm gate. The default `GATE_CM` is 30; use 60 for
   the demo mount if the athlete stands further back. Clamp range is 5-200.
6. `STIM` arms the ultrasonic. A hand inside the gate returns one
   `HIT <ms> <cm>`; nothing inside 5 s returns `TIMEOUT`.
7. `LED G|Y|R|0` sets exactly one LED, or all off. `SHOW <ms> <grade> <best>`
   draws the OLED result and disarms.

### OLED "I2C NONE"

`I2C NONE` means the Uno found nothing on the I2C bus. The firmware keeps
running; only the OLED is missing. Check:

- SDA -> A4, SCL -> A5, VCC -> 5 V, GND -> GND (module order is sometimes
  SDA/SCK/VCC/GND, verify the silkscreen),
- try address `0x3D` instead of `0x3C` in `reflex_fw/reflex_fw.ino`,
- try `OLED_TYPE 2` (SH1106) if the screen stays blank,
- rescan: `python scripts/check_rig.py --seconds 5` should print the `I2C`
  line from boot.

Do not rewire on stage. The LEDs and the app still work with no OLED.

---

## 3. LED ownership and release

The Arduino LED is a **momentary mirror**, never a latch.

- On connect / `READY`: `DEMO 0` turns the standalone LED loop off; the app
  owns the LEDs and starts with them off.
- On start, arm, stop and fault: the app explicitly sends `LED 0`.
- On a result: the app sends the feedback colour, then schedules **`LED 0`
  after `LED_GLOW_MS` (default 900 ms)**. A newer glow cancels the older
  timer, so a stale timer can never turn off a newer result.
- On `SerialRig.stop`: the app sends **`DISARM` then `LED 0`**, flushes, and
  closes the port. Shutting the app down cannot leave an LED lit.
- The LED is never the only copy of the state. The same colour is in the
  result payload (`edge`, `led`) and on the page edge glow.

Inspect `/api/health` -> `rig`:

```json
"led": "0",
"led_log": ["LED G", "LED 0"],
"glow_active": false
```

`glow_active` is true only while the 900 ms timer is pending.

### Colour mapping

| Result | `edge.color` | `edge.ms` | `led` (wire) | Page class |
|---|---|---|---|---|
| Racing G / Y / R | `G` / `Y` / `R` | 900 | `G` / `Y` / `R` | `.edge-good` / `.edge-warn` / `.edge-bad` |
| Racing false start | `F` | 900 | `R` | `.edge-false` |
| Racing miss/timeout (`M`) | `R` | 900 | `R` | `.edge-bad` |
| Boxing correct (any RT) | `G` | 900 | `G` | `.edge-good` |
| Boxing wrong or miss | `R` | 900 | `R` | `.edge-bad` |
| Boxing false start | `F` | 900 | `R` | `.edge-false` |

**Boxing is action-first.** Do not use the RT grade for the edge/LED: a slow
but correct cue is green feedback. The `grade` field (and the OLED `SHOW`
grade) still carries the RT grade (`G`/`Y`/`R`) for the result card.

Every result payload adds these two fields and leaves the existing fields
untouched:

```json
{
  "type": "result",
  "grade": "R",
  "correct": true,
  "edge": {"color": "G", "ms": 900},
  "led": "G"
}
```

The bot result carries the same shape so the UI can reuse one renderer, but
a bot turn never touches the physical LED.

`DIST` events are also broadcast on the websocket as
`{"type":"rig","event":"DIST","args":[42],"raw":"DIST 42"}`, and the state
snapshot includes `last_distance_cm`.

---

## 4. Page edge glow

`frontend/edge.css` and `frontend/edge.js` are self-contained. Add both tags
to a page (the two tags the pages need):

```html
<link rel="stylesheet" href="/static/edge.css">
<script src="/static/edge.js" defer></script>
```

The stylesheet creates a fixed full-viewport `#edge-glow` with
`pointer-events: none`, `z-index: 1100`, opacity 0, and
`box-shadow: inset 0 0 140px 36px var(--edge-color)`. The classes are:

| Class | Meaning |
|---|---|
| `.edge-good` | green signal |
| `.edge-warn` | amber |
| `.edge-bad` | red |
| `.edge-false` | violet false start |

It fades with `--dur-edge` (300 ms), reduces to a near-instant transition
under `prefers-reduced-motion: reduce`, and becomes a solid inset ring under
`prefers-contrast: more`. Fixed positioning means no layout shift.

The script creates the overlay, injects the stylesheet if the page forgot the
`<link>`, and is safe to load twice. It never touches the WebSocket.

### API

```js
window.Split2ndEdge = {
  flash(color, ms = 900),  // G/Y/R/F (M maps to red); auto-clears
  clear()                  // hide immediately, cancel the timer
};
```

Call it from the result handler **after** the normal result rendering:

```js
Split2ndEdge.flash(msg.edge?.color || msg.grade, msg.edge?.ms || 900);
```

Do not send `LED` websocket commands from the page. LED ownership lives in
`backend/rig.py`; the page only mirrors the result payload. The physical LED
and the screen edge are two presentations of one decision, so they cannot
disagree.

The app pages will link the two tags; this module does not edit
`index.html`, `play.html`, `controller.js` or `play.js`.

---

## 5. Quick reference

```powershell
# camera
.venv\Scripts\python scripts\check_camera.py --seconds 4

# Arduino, one owner at a time
.venv\Scripts\python scripts\check_rig.py --port COM6 --seconds 10 --gate 60 --led

# app (laptop-only HTTP)
.venv\Scripts\python scripts\run.py --vision local --rig auto --no-tls --camera 0

# health
curl http://127.0.0.1:8443/api/health
```

Checklist:

- [ ] `camera.index = 0`, `camera.backend = "DSHOW"`, `camera.width = 640`,
      `camera.height = 480`, `camera.fps = 30`.
- [ ] `vision.mode = "local"`, `vision.fps > 0`, `vision.pose_backend = "tasks"`.
- [ ] Head and both hands in frame; no black frame.
- [ ] Arduino IDE Serial Monitor closed; `rig.port_busy = false`.
- [ ] `rig.firmware = "reflex-fw 1.0"`; `rig.i2c = "0x3C"` or `"NONE"` (OLED only).
- [ ] `DEMO 0`, `PONG`, `VER`, `DIST` seen in `check_rig.py`.
- [ ] `GATE 60` gives `OK`; a hand inside 60 cm gives one `HIT`.
- [ ] One result lights the LED in the edge colour, then `led_log` ends
      `LED 0` after ~900 ms; `glow_active` returns to false.
- [ ] `Split2ndEdge.flash` paints the same colour on the page.
