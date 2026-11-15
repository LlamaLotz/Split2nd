# Demo runbook — laptop rig

Current rig: **laptop integrated webcam + laptop screen + Arduino/ultrasonic on USB.**
The controller page is the display and the scoreboard. There is **no phone client** in the current demo path. Phone, Tailscale, TLS and the CA page are backlog.

---

## T−20 minutes: pre-flight

```powershell
cd C:\Users\Shiva\Second2nd
.venv\Scripts\python scripts\check_pose_landmarker.py
.venv\Scripts\python scripts\check_camera.py --seconds 3
.venv\Scripts\python scripts\check_rig.py --seconds 10 --gate 30
.venv\Scripts\python scripts\run.py --vision local --rig auto --no-tls
```

Open `http://127.0.0.1:8443/` on the laptop.

- [ ] Pose: `check_pose_landmarker.py` prints `POSE_LANDMARKER_OK`.
- [ ] Camera: `/api/health` shows `vision.mode: "local"`, `vision.fps > 0`, `vision.pose_ok: true`, `vision.pose_backend: "tasks"`.
- [ ] The controller preview shows the athlete's upper body and skeleton, not just a corner of the room.
- [ ] Rig: `/api/health` shows `rig.backend: "serial"`, `rig.connected: true`, `rig.ready: true`. The log should show `DEMO 0` sent after `READY`.
- [ ] `PING` gives `PONG`, `VER` gives `reflex-fw 1.0`, `DIST` gives a number. `scripts/check_rig.py` prints these.
- [ ] Wave at the ultrasonic: LEDs go green/yellow/red; the OLED shows the distance.
- [ ] Seed once (`scripts/seed.py` or the controller's seed button) so the racing and boxing leaderboards are not empty.
- [ ] Start one racing rep from the controller: lights paint on the controller, result lands, LEDs/OLED mirror the grade.
- [ ] Start one boxing set with 2 cues in fake mode if the camera is not warm, then switch back to local. Do not debug MediaPipe on stage.
- [ ] Close Slack/Teams/Chrome tabs. Plug the laptop in. Turn on Do Not Disturb.
- [ ] Record a 30-second backup video of one racing rep and one boxing cue.

**Fallback if the local camera is not green by T−10:**

1. `scripts/run.py --vision fake --rig auto --no-tls` for the UI/reward demo.
2. Say clearly: "The classifier is running on synthetic landmarks; the real camera is being checked." Do not claim a camera measurement you do not have.
3. If the Arduino is not green, `--rig fake`; the UI still grades. Say the hardware link is being wired.

---

## Three-minute script

**0:00 — the problem.**
> Reaction time decides races, saves and fights, but the tools that train it cost thousands. We built the cheap version with a webcam, a laptop, and a $25 Arduino.

**0:20 — one racing rep.**
1. Add the judge with **+ Athlete**, or use a demo profile.
2. Put them in front of the laptop camera, upper body in frame.
3. Press **START REP**. The lights paint on the controller, go out, they move.
4. The controller shows the RT, grade, XP and one evidence tip. The Arduino LEDs mirror it; the OLED shows the number.

Say:
> The controller page paints the light and acks the paint. We anchor the timer on that ack. The webcam frame timestamps and the timer are on the same laptop clock. The Arduino's ultrasonic is the hardware-timed cross-check: it says 263 ms, the camera says 272.

**1:10 — the leaderboard.**
Their name is on the board. Point at the seeded names; the board sorts racing by best, ties by median, and marks demo profiles honestly.

**1:30 — boxing set (the new mode).**
1. Select the athlete, choose **Boxing**, stance **Orthodox**, press **Start set**.
2. The controller paints the cue (Jab, Cross, Left hook, Slip right, Left uppercut, Block, Right hook, Slip left, Right uppercut, Cross).
3. The athlete shadow-boxes the cue. Each cue has a 1.4 s window, 500–800 ms pre-cue delay, and auto-advances.
4. After each cue the controller shows: expected vs detected, correct/wrong/miss, confidence, reaction ms, execution ms, combo, XP, and progress `n/10`.
5. After cue 10 the controller shows `session_end`: stars, chest, mascot, the full report, and **one** evidence-backed tip.
6. Open the boxing leaderboard (`mode=boxing`): it sorts by accuracy first, then median reaction time. The ghost panel shows the last/best session trace, not a social graph.

Say:
> The classifier is rule-based, not a trained model. It normalizes pose landmarks by shoulder width and asks whether the wrist depth dropped and the elbow opened (jab/cross), whether the wrist crossed the body with a bent elbow (hook), whether it rose from below the shoulder (uppercut), whether the head left the line while the hands stayed high (slip), or whether both hands were high and still (block). Every threshold is in one block, and the result carries the confidence so you can see when it is guessing.

**2:20 — evidence.**
Open the tip. Read the headline, point at the source.
> Every coaching line is tied to a published source. We do not let a model invent citations. The biomechanics source is the cross/hook/uppercut study; the fatigue source is the 2025 lower-limb study.

**2:40 — limits.**
> Single camera, 10–30 fps, no depth sensor. This is a gross-action label, not punch force and not head impact. Video-only punch classification is known to degrade with motion blur and occlusion, which is why the honest claim is the timing pipeline, the gross action, and the within-athlete trend — not a punch meter.

**2:55 — close.**
> Cheap hardware, a laptop you already own, and a measurement we can defend. Train the first 250 milliseconds.

---

## Boxing-specific checks

- The cue must be visible before the athlete moves. If the controller shows the cue late, press **Stop** and start again; do not debug the classifier.
- If a cue is consistently wrong on the real camera, remove it from the default set and restart the backend. The synthetic tests say all nine actions classify at 10–30 fps; the Windows camera is the gate.
- A correct block may show `reaction_ms: null` and grade `Y` at 10–12 fps. That is expected: a held block has no single onset frame. It scores the action, gives no speed XP, and does not pretend to measure RT.
- If the classifier says `unknown`/miss on a cue the athlete clearly threw, check:
  1. both wrists are in frame,
  2. the camera sees the full upper body,
  3. the athlete is facing the camera (not side-on),
  4. `vision.fps` is above 8,
  5. the cue was not painted before the athlete was ready.
- Do not lean on the ultrasonic `HIT` for boxing scoring. It is a cross-check; shadow-boxing may never trip the gate, and the 25 ms ping interval quantizes it.

---

## Failure recovery (in order)

| What broke | Move |
|---|---|
| Pose model fails | `check_pose_landmarker.py`; if it fails, the app falls back to legacy pose then motion. Motion-only cannot classify boxing; use `--vision fake` for the UI demo. |
| Camera does not open | `scripts/check_camera.py --scan`; try another USB port; then `--vision fake` and say so. |
| Camera opens but pose is not detected | More light, plain background, full upper body, step back. If still no pose, `--vision fake` for the UI demo. |
| No LEDs/OLED | `--rig fake`; the UI still grades. Do not rewire on stage. |
| `DIST`/`VER` no reply | Re-run `scripts/check_rig.py --port COMx`; close the Arduino IDE Serial Monitor; use a data cable. |
| Cue paints but no result | Check `vision.fps`, `vision.pose_ok`, `vision.pose_backend`; check the result phase in `/api/health`. |
| A cue is consistently misclassified | Remove that cue from `DEFAULT_SET` in `backend/boxing.py`, restart, continue with the remaining cues. |
| Leaderboard empty | Run `scripts/seed.py` or press the seed button. |
| Everything slow | Close other tabs, drop camera resolution, use `--vision fake` for the remainder. |

---

## Lines that are true and safe to say

- "Pretrained MediaPipe Pose Landmarker for pose. Our work is the timing pipeline, the rule-based gross-action classifier, the scoring, and the hardware cross-check."
- "The boxing classifier is rule-based, not a trained model. Every threshold is inspectable."
- "The ultrasonic is microsecond-timed on the Arduino, but sampled every 25 ms, so its own quantization is ±25 ms."
- "A single camera gives a gross action label, not punch force. Video-only punch classification is known to be limited by blur and occlusion."
- "There is no true simultaneous multiplayer. Turn-based is what is physically honest."

## Lines not to say

- "AI measures your punch force" — it does not.
- "±1 ms accuracy" — no consumer camera setup is.
- "We trained a model" — not in this build.
- "It works on any camera angle" — validate the stance and framing first.
- "It works over the venue Wi-Fi" — the current rig is local; do not claim a network path you have not tested.
