# Demo runbook

One page for the person holding the laptop on stage. Print it or keep it on the phone.

---

## T−30 minutes: pre-flight

```powershell
cd C:\Users\Shiva\Second2nd
.venv\Scripts\python scripts\check_camera.py --seconds 3
.venv\Scripts\python scripts\check_rig.py --seconds 10 --gate 30
.venv\Scripts\python scripts\serve_ca.py           # second terminal, for the phones
.venv\Scripts\python scripts\run.py                # 8443, HTTPS
```

- [ ] Laptop and phone on the **same** network. Prefer the Windows Mobile Hotspot.
- [ ] Phone opens `http://<laptop-ip>:8080/` → install CA → **trust it**.
- [ ] Phone opens `https://<laptop-ip>:8443/cam` → **Start camera** → frames counter increases.
- [ ] Phone opens `/play?player=<id>` → camera starts → full screen → Guided Access on.
- [ ] Controller `/` shows `RIG COMx` green and `VISION ... fps` moving.
- [ ] Start one rep from the controller, confirm the phone paints and a result lands.
- [ ] Seed demo data once (`scripts/seed.py`) so the leaderboard is not empty.
- [ ] Record a 30-second backup video of a working rep.
- [ ] Close Slack/Teams/Chrome tabs. Plug the phone in. Turn on Do Not Disturb.

If the phone path is not green by T−20, switch to **laptop camera + laptop display**:

1. Controller → Camera → **Laptop** (or start with `--vision local`).
2. Start a rep; the F1 overlay runs on the laptop screen.
3. The phone is no longer required. Do not debug on stage.

If the Arduino is not green, start with `--rig fake`. Everything still demos; say the hardware link is being wired by the hardware lead (or leave it out).

---

## Three-minute script

**0:00 — the problem.**
> Reaction time decides races, saves and fights, but the tools that train it cost thousands. We built the cheap version: your phone is the sensor and the scoreboard.

**0:20 — one live rep.**
1. Ask a judge for their name. Add them with **+ Athlete** (or use the demo profile).
2. Hand them the phone. Front camera at eye level, step back so their shoulders are in frame.
3. On the controller, press **START REP**. The lights come on one by one, go out, they move.
4. Phone shows the RT, grade, XP and one evidence tip. The Arduino LEDs mirror it; the OLED shows the number.

Say:
> The phone paints the light and acks the paint. We anchor the timer on that ack, so the camera timestamps and the Arduino's ultrasonic pulse share one time origin. The ultrasonic is the hardware-timed cross-check: it says 263 ms, the camera says 272.

**1:10 — the leaderboard.**
Their name is now on the board. Point at the seeded names; the board sorts by best, ties by median, and marks demo profiles honestly.

**1:30 — head-to-head.**
Press **Create vs BOT** → **Start match**. Five reps each, alternating. The opponent's last RT is drawn as the ghost target on the next rep. A second phone can join the room code and the bot drops out.

> Winner is decided on median, not one lucky rep. One fluke should not win a match.

**2:10 — evidence.**
Open the tip. Read the headline, tap the source.
> Every coaching line is tied to a published source. We do not let a model invent citations. This one is the sleep meta-analysis; this one is the 100 ms false-start rule.

**2:30 — limits.**
> Absolute times include display and camera latency. Device reliability is moderate. So the score is your median against your own baseline, not a population norm. That is why the streak and the ghost matter more than a single number.

**2:50 — close.**
> Cheap hardware, a phone you already own, and a measurement we can defend. Train the first 250 milliseconds.

---

## Failure recovery (in order)

| What broke | Move |
|---|---|
| Phone camera won't start | Controller → Camera → **Laptop**. Continue. Mention the phone path as the design. |
| Phone disconnects mid-rep | The rep faults cleanly with a banner. Reconnect, start again. |
| Frames but no result | Look at `/api/health` → `vision.fps`. If 0, switch to Laptop. |
| No LEDs/OLED | `--rig fake`; the UI still grades. Do not rewire on stage. |
| Leaderboard empty | `POST /api/dev/seed` from the dev panel, or run `scripts/seed.py`. |
| Room start says "busy" | A result is still on screen; wait 2 seconds. |
| Everything slow | Close other tabs, drop the phone to 480p in `play.js` (one constant), or use the laptop camera. |

---

## Lines that are true and safe to say

- "Pretrained pose model for body tracking; our work is the timing pipeline, the sensor fusion, and the scoring."
- "The phone and the laptop disagree by about 10 ms on a reach; that is the number we report."
- "The ultrasonic is microsecond-timed on the Arduino, but sampled every 25 ms, so its own quantization is ±25 ms."
- "We do not claim to have trained a model yet. We log labeled reps so that is the next step."
- "There is no true simultaneous multiplayer. Turn-based is what is physically honest with two phones."

## Lines not to say

- "AI measures your reflexes" — it measures a movement onset from frames.
- "±1 ms accuracy" — no consumer camera setup is.
- "We trained a model" — not in this build.
- "It works over the venue Wi-Fi" — verify it, or use the hotspot.
