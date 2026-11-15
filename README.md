# Split2nd

A reflex trainer where **the athlete's phone is the sensor and the scoreboard**, the laptop is the engine, and an Arduino Uno is the hardware-timed I/O unit.

The athlete sees F1-style lights, reacts, and gets a reaction time, a green/yellow/red grade, XP, a ghost target, and one evidence-backed coaching tip. The same engine runs solo reps and a head-to-head match against a bot or a second phone.

```
        iPhone 16 / iPad (Safari, HTTPS)
        ┌──────────────────────────────────┐
        │ fullscreen lights · front camera │
        │ result card · XP · ghost target  │
        └───────▲──────────────────┬───────┘
   frames + ts  │                  │ painted ack, commands
                │                  ▼
        ┌───────┴──── ThinkPad (Windows) ─────────────┐
        │ FastAPI · MediaPipe / motion fallback       │
        │ onset detector · XP/streak/leaderboard      │
        │ JSON store · evidence engine · MJPEG preview│
        └───────┬─────────────────────────────────────┘
                │ USB serial 115200 (the only link that works)
          Arduino Uno R3
          ├─ LEDs G/Y/R
          ├─ OLED (U8g2)
          └─ HC-SR04 ultrasonic (Z channel)
```

The Uno never talks to the iPhone. iOS exposes no CDC-ACM serial port to apps and Safari has no Web Serial API, so a USB-C adapter into the phone is a charging cable. The laptop is the bridge.

---

## Quick start (Windows demo laptop)

```powershell
cd C:\Users\Shiva\Second2nd
py -3.12 -m venv .venv
.venv\Scripts\pip install -r requirements.txt

# one-time TLS material (add every IP the phone will use)
.venv\Scripts\python scripts\make_certs.py --ip 192.168.0.80 --ip 192.168.137.1

# trust the CA on this laptop
certutil -addstore -user Root certs\ca.crt

# optional demo history so the streak/leaderboard screens look alive
.venv\Scripts\python scripts\seed.py

.venv\Scripts\python scripts\run.py
```

Open on the laptop: `https://192.168.0.80:8443/`
Open on the phone: `https://192.168.0.80:8443/play?player=<id>`
Camera/HTTPS gate: `https://192.168.0.80:8443/cam`

The runner prints the exact URLs. If the venue Wi-Fi looks hostile, turn on the **Windows Mobile Hotspot**, connect the phone, and use `https://192.168.137.1:8443/play` (that IP is already in the generated cert).

> If `openssl` is not on PATH on Windows, generate the certs in WSL/Linux with
> `python scripts/make_certs.py --ip 192.168.0.80 --ip 192.168.137.1` and copy the
> `certs/` folder to the Windows checkout. The certs are machine-independent.
>
> Allow Python through Windows Defender on **Private** networks, or the phone will
> time out even with the right URL.

### iOS trust, once per device

The easiest path is the tiny plain-HTTP CA page (no certificate warning):

```powershell
.venv\Scripts\python scripts\serve_ca.py
```

On the phone, open `http://<laptop-ip>:8080/`, download the profile, install it, then enable it under Settings → General → About → Certificate Trust Settings.

Or open `https://<laptop-ip>:8443/ca.crt` and accept the warning first.

1. In Safari, open the CA page.
2. Settings → **Profile Downloaded** → Install.
3. Settings → General → About → **Certificate Trust Settings** → enable *Split2nd Local CA*.
4. Reload `/play`, tap **Enable camera**.

If the camera does not start, open `/cam` first. It prints whether the page is a secure context, whether `getUserMedia` exists, and whether frames are reaching the server.

### No hardware yet? Run everything fake

```powershell
.venv\Scripts\python scripts\run.py --vision fake --rig fake --no-tls
```

Plain HTTP on `http://127.0.0.1:8443` runs the full state machine, UI, XP, leaderboard and versus mode with synthetic motion. The phone camera needs HTTPS; the fake mode does not.

### Useful bring-up scripts

```powershell
.venv\Scripts\python scripts\check_camera.py --scan
.venv\Scripts\python scripts\check_rig.py --port COM4 --seconds 15 --led
.venv\Scripts\python test_rig.py COM4        # firmware acceptance test
.venv\Scripts\python -m pytest -q
```

Upload `reflex_fw/reflex_fw.ino` from the Arduino IDE first, then close the IDE Serial Monitor. `test_rig.py` exercises the real protocol: boot lines, `VER`, demo telemetry, `GATE`, `DIST`, `STIM`/`HIT`, `LED`, `SHOW`, and the overlong-line error.

---

## Pages

| Path | Role | What it does |
|---|---|---|
| `/` | controller | athlete picker, start/stop, gate, camera source, live preview, leaderboard, rep log, evidence, rooms, dev controls |
| `/play` | player phone | camera permission, fullscreen lights, result/XP/tip, ghost bar, self-start, versus |
| `/cam` | diagnostic | HTTPS/CA/getUserMedia/WS/frame-rate check |
| `/video` | MJPEG | annotated preview for the controller |
| `/api/*` | REST | health, players, leaderboard, history, evidence, rooms |
| `/ws` | WebSocket | all realtime traffic |

The player link is `https://<host>/play?player=<player_id>&room=<CODE>`. Use **Player link** on the controller to copy it.

---

## Timing model (why the code looks like this)

1. The backend enters `wait` and sends `{type:"schedule", delay_ms}` **to the display client only**.
2. The display paints locally with `setTimeout`, then immediately sends `{type:"painted", ts, clock}`.
   Do **not** wait for `requestAnimationFrame` before acking: the Arduino's `STIM` is sent on the ack, and a two-frame delay biases the ultrasonic `HIT` low by ~33 ms.
3. If the camera and the display are the **same client**, `t0 = ts` and every frame carries the same `performance.now()` clock. No clock sync, no RTT/2 error.
4. If the display is the laptop, `t0 = ack receive time` and local frames use backend `perf_counter`.
5. If the camera is remote but the display is not, `offset = receive_time − ts` maps frame timestamps onto the backend clock for that rep.
6. `RT = onset − t0`. `< 100 ms` is a false start; no onset inside 1.5 s is a miss.
7. The Arduino measures `HIT` from its own `STIM` receipt in microseconds. It is the cross-check, and the fallback when the camera has no frames.

Both channels start within a few milliseconds of the same paint, so `vision RT − HIT` is a real validity number. Display and camera capture latency are common additive biases; report them instead of hiding them.

### Vision sources

| `VISION=` | Behaviour |
|---|---|
| `auto` | laptop camera if OpenCV can open it, otherwise fake samples |
| `local` | laptop camera only |
| `remote` | phone frames only (laptop camera off) |
| `fake` | synthetic 60 Hz samples; the full drill loop runs with no camera |

MediaPipe Pose (lite) is optional. If it cannot load, the engine falls back to frame-difference motion energy, which is enough for onset timing in the racing mode. Pose is what draws the skeleton and unlocks direction classification later.

> MediaPipe downloads `pose_landmark_lite.tflite` on first use and caches it inside the venv. Warm it up before the venue (open `/cam` once and wait for `pose_ok: true`), or plan on the motion fallback if the laptop is offline.

---

## Scoring, XP, streaks

| Result | Grade | XP |
|---|---|---|
| ≤ 250 ms | **G** | 10 base + speed bonus + PB/streak |
| 251–350 ms | **Y** | same |
| > 350 ms | **R** | same |
| < 100 ms or movement before the light | **F** (false start) | 0 |
| no onset in 1.5 s | **M** (miss) | 0 |

`speed bonus = clamp((400 − rt) / 10, 0, 40)`, `PB = +20`, `streak = +3/day` capped at 7 days. The result card shows the full breakdown.

Camera-mode absolute values include roughly 30–60 ms of display/capture latency, so the same athlete grades one band slower than a lab timer. If you want grades that match the lab bands, start with `GREEN_MS=300 YELLOW_MS=400` and tune on the rig; the defaults stay at the spec's 250/350.

Streak = consecutive calendar days with at least one recorded rep. Levels are thresholds in `backend/xp.py`. The leaderboard sorts by **best** RT, ties by median, and marks seeded profiles `demo`.

---

## Head-to-head

1. Controller → **Create vs BOT** → 4-letter room code.
2. **Start match**: 5 reps each, alternating. The winner is decided by **median** RT, not one lucky rep.
3. A second phone opens `/play?player=<id>&room=<CODE>` and joins; the bot drops out.
4. The opponent's last RT becomes the **ghost target** on the next rep.
5. Bot turns run automatically; human turns can auto-advance.

There is no true simultaneous mode. Two athletes cannot react to the same light with sub-frame sync, and pretending otherwise would be dishonest.

---

## Arduino protocol (115200, newline ASCII)

| Direction | Message | Meaning |
|---|---|---|
| PC → Uno | `PING` | reply `PONG` |
| PC → Uno | `VER` | reply `VER reflex-fw 1.0` |
| PC → Uno | `DEMO 0` / `DEMO 1` | demo telemetry off/on (send `DEMO 0` after `READY`) |
| PC → Uno | `GATE <cm>` | ultrasonic trigger distance, 5–200 cm |
| PC → Uno | `DIST` | one distance reading: `DIST <cm>` or `DIST -1` |
| PC → Uno | `STIM` | arm the microsecond timer; turns demo off |
| PC → Uno | `DISARM` | stop watching the gate |
| PC → Uno | `LED G\|Y\|R\|0` | feedback LED |
| PC → Uno | `SHOW <ms> <G\|Y\|R\|F> <best>` | OLED result (the app maps a miss to `R`) |
| Uno → PC | `READY` | boot complete |
| Uno → PC | `I2C 0x3C` / `I2C NONE` | OLED address at boot |
| Uno → PC | `PONG` / `OK` / `ERR ...` | command replies |
| Uno → PC | `DEMO <cm>` | distance every 150 ms while demo telemetry is on |
| Uno → PC | `HIT <ms> <cm>` | gate tripped since `STIM` |
| Uno → PC | `TIMEOUT` | armed for 5 s with no hit |

The firmware is **`reflex_fw/reflex_fw.ino`**; the authoritative hardware contract is [`arduino-firmware-spec.md`](arduino-firmware-spec.md). There is no rotary encoder and no button on the real unit — LED, OLED and the ultrasonic are the whole physical interface. `test_rig.py` is the serial acceptance test for the sketch.

Serial details that bite:

- Genuine Uno: ATmega16U2, Windows `usbser.sys`. Clone: CH340, install the driver **before** the venue.
- Close the Arduino IDE Serial Monitor; only one process can hold `COMx`.
- Opening the port resets the board; `READY` arrives ~2 s later. The rig reads until `READY`, then sends `DEMO 0` and `PING`.
- `DIST` is the live calibration readout; `STIM` turns demo telemetry off automatically.
- Use a data cable, not a charge-only one. Prefer a direct port over an unpowered hub.

---

## Realtime API

`/ws?role=controller|player|camera&player_id=<id>&room=<CODE>`

Server → client:

```
hello      {version, player, players, leaderboard, evidence, config, state, clients}
state      {phase, msg, player, source, display, gate_cm, room, result}
schedule   {delay_ms, lights, light_interval_ms, player, rep, room, display, source}
result     {player, rt_ms, grade, false_start, miss, hit_ms, xp, new_pb, best_ms,
            stats, ghost_ms, ghost_delta, tip, room, bot}
room       {code, target, status, turn, players, winner, ghost_ms}
room_done  {room}
fault      {message}
presence   {clients}
rig        {event, args, raw}
```

Client → server:

```
{type:"frame", ts, clock, jpeg}     JPEG base64, one in flight at a time
{type:"painted", ts, clock}         stimulus paint ack
{type:"ready", camera:true}         getUserMedia succeeded
{cmd:"start", player_id, room, auto}
{cmd:"stop"} {cmd:"select"} {cmd:"source"} {cmd:"auto"} {cmd:"gate"}
{cmd:"room", player_id, bot} {cmd:"join", room, player_id}
{cmd:"sim", kind} {cmd:"led", value}
```

REST: `GET /api/health`, `GET/POST /api/players`, `GET /api/leaderboard?mode=racing`, `GET /api/players/{id}`, `GET /api/evidence`, `GET /api/history?limit=`, `POST /api/rooms`, `POST /api/rooms/{code}/join`, `POST /api/rig/gate`, `POST /api/dev/sim`, `POST /api/dev/seed`.

The websocket has no origin auth. It is a LAN demo tool; do not expose it to the internet.

---

## Evidence engine

Tips are chosen from the athlete's own numbers and always carry a real source. No runtime web search, no model-written citations.

| Claim in the app | Source |
|---|---|
| <100 ms is anticipation, not reaction | World Athletics / IAAF Sprint Start Research |
| Short sleep slows RT and raises variability | Lim & Dinges 2010, *Psychological Bulletin* |
| Caffeine 3–6 mg/kg ~60 min pre; simple-RT effect is mixed | ISSN position stand 2021; Grgic et al. 2021, *Nutrients* |
| More choices = slower | Hick 1952 |
| Speed trades against accuracy | Fitts 1954 |
| Compare within-athlete, not to norms | BlazePod reliability study, *Balt J Sport Health Sci* |
| RT = stimulus + decision + motor, affected differently by fatigue | Welford 1980 |

Full links live in `backend/evidence.py` and at `/api/evidence`. The About/foot note says it plainly: single-athlete data, moderate device reliability, absolute values include display and camera latency.

---

## Repository layout

```
backend/
  main.py        FastAPI, WS, MJPEG, REST, static pages
  config.py      every tunable + env switch
  rig.py         FakeRig / SerialRig + auto-detect + handshake
  vision.py      local/remote/fake sources, pose + motion fallback, onset
  drills.py      state machine, scoring, rooms/versus
  store.py       JSON persistence, XP/streak/leaderboard
  evidence.py    curated citations + feedback rules
  xp.py          pure XP/level/streak/median math
  net.py         websocket client registry
frontend/
  index.html     controller / scoreboard
  play.html      phone display + camera
  cam.html       HTTPS/camera diagnostic
  app.css        shared UI
  controller.js  play.js  cam.js  common.js
scripts/
  run.py         uvicorn with optional TLS
  make_certs.py  local CA + SAN cert for iOS
  check_rig.py   serial bring-up
  check_camera.py camera + pose bring-up
  seed.py        demo history
tests/
  test_core.py   onset, XP, streak, store, rooms, evidence
docs/
  reflex-trainer-master-spec.md
```

Runtime data (`data/store.json`) and `certs/` are gitignored.

---

## Verification (hardware-free)

```bash
python -m pytest -q
python scripts/run.py --vision fake --rig fake --no-tls
# open http://127.0.0.1:8443/  -> pick an athlete -> START REP
```

The fake vision generates an onset ~200–340 ms after `STIM`, so a rep completes with a plausible grade and XP. The vs-BOT room alternates human/bot results and finishes after 5 reps each.

The remote-camera path was verified with a synthetic JPEG stream: `ready → start → schedule → painted → frames → onset → result`, `RT ≈ 270 ms`, `HIT` as the cross-check, preview live at ~28 fps.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Camera button does nothing on iPhone | Not HTTPS or CA not trusted. Open `/cam`; check the secure-context line. |
| `getUserMedia` missing | Use Safari on iOS 15+. Chrome on iOS is WebKit and has the same API, but Safari is the tested path. |
| Page loads but WS reconnects forever | Wrong IP / client isolation on venue Wi-Fi. Use the laptop's mobile hotspot. |
| Phone cannot reach the server at all | Windows Defender: allow Python on **Private** networks for the 8443 listener. |
| Phone frames arrive but FPS is low | Close other tabs, keep the phone plugged in, drop JPEG quality to 0.5 in `play.js`. |
| `No display connected` | No phone is registered as `player` and no controller page is open. Open `/` or `/play`. |
| `Phone camera is not streaming` | Tap **Enable camera**, check the phone's permission, open `/cam`. |
| `Display did not acknowledge` | The phone tab was backgrounded or Safari was killed. Keep it foreground + Guided Access. |
| `COMx` missing | CH340 driver, data cable, or Arduino IDE Serial Monitor still open. |
| Ultrasonic never trips | `GATE` too low, sensor aimed at a soft sleeve, hand not entering the cone. `python scripts/check_rig.py --seconds 15`. |
| Pose jittery / no skeleton | Plain background, contrasting clothes, full upper body, light. Motion fallback still times onset. |
| Old JS after an edit | The server sends `Cache-Control: no-store`; hard-reload once if a proxy cached it. |
| Arduino resets on every save | Do not run `--reload` during a drill. |

---

## Honest limits

- This is a single-athlete, consumer-camera measurement. Absolute RTs include display and camera latency; compare medians across sessions, not single reps against population norms.
- The pose model is pretrained MediaPipe. The project's technical claim is the **timing pipeline and sensor fusion**, not a trained classifier. Rep data is logged with timestamps, so a direction classifier and leave-one-athlete-out evaluation are the natural next step.
- The ultrasonic channel is quantized by its 25 ms ping interval; the OLED redraw blocks ~30–40 ms, so it only runs when the rig is not armed.
- Multiplayer is turn-based by design. Sub-frame sync across two phones is not physically honest.

---

## License / credits

Built for OnHacks. Hardware spec, pin map, and firmware reference in `docs/reflex-trainer-master-spec.md`. Citations are linked in `backend/evidence.py`.
