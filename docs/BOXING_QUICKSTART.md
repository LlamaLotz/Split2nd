# Boxing demo quickstart (laptop webcam + COM6 ultrasonic)

## 1. Arduino

Upload `reflex_fw/reflex_fw.ino` to the Uno on **COM6** (Arduino IDE).
Close the Arduino IDE **Serial Monitor** before starting Python — only one
program can own the port.

The firmware already supports the two commands this demo needs:

- `DIST` → one real distance reading, `DIST <cm>`
- `DEMO <cm>` → the 150 ms continuous stream (used as a fallback)

The boxing coach polls `DIST` while idle and uses the real `HIT <cm>` at the
gate during the rep. Both feed a timestamped Z history; each pose frame gets
the nearest fresh reading. No predicted/pose Z is used when the sensor has a
real reading.

## 2. Run

Windows PowerShell:

```powershell
$env:GATE_CM="60"   # optional: wide enough for a full-extension punch
.\.venv\Scripts\python scripts\run.py --no-tls --vision local --rig serial --rig-port COM6
```

Windows cmd:

```bat
set GATE_CM=60
.venv\Scripts\python scripts\run.py --no-tls --vision local --rig serial --rig-port COM6
```

The default gate is 30 cm. For a full-extension punch toward the laptop,
set `GATE_CM=60` (or whatever distance your fist reaches) so the real
ultrasonic `HIT` fires and the Z drop is measured.

Open `http://localhost:8443/boxing`.

If the camera or port is missing, the UI can still be shown with
`--vision fake --rig fake`; the punches will be simulated/unknown, not real.

## 3. Demo

1. Stand arm's length from the laptop, head-to-hips in frame, guard up.
2. Press **START**.
3. The cue appears at GO. Throw the called punch, then snap back to guard.
4. The page shows: detected punch, expected punch, reaction time, confidence,
   combo, real ultrasonic Z and the Z drop, plus one coaching suggestion.
5. After 10 cues, the report shows accuracy, median RT, max combo, stars and
   the session tip.

## What is measured

- **X/Y**: MediaPipe Pose Landmarker (Tasks API, lite model).
- **Z**: real HC-SR04 distance from the Arduino on COM6 — not a model
  prediction. `sensor_drop_cm` is the primary straight-punch condition when
  the sensor is fresh; the monocular pose depth is only the fallback.
- **Reaction time**: cue paint → first sustained movement of the expected hand.
- **Execution time**: duration of the action.
- **Accuracy / combo**: correct vs wrong vs miss, per cue.

## Six punches

The classifier covers `jab`, `cross`, `left/right hook`, `left/right uppercut`,
plus slip/block cues. Orthodox by default; the stance button swaps lead/rear.

Tuning lives in `backend/boxing.py` → `THRESHOLDS`:
`straight_depth_drop`, `sensor_straight_drop_cm`, `hook_cross_travel`,
`uppercut_rise`, etc. If a punch is unreliable on the real camera, remove that
cue from `DEFAULT_SET` rather than making the demo lie.

## Adaption artifact

The Olympic 2,278-clip dataset is separate:
`olympic_boxing_adaption.zip` (1,822 train / 456 held-out val montages).
It is the fine-tuning artifact for the next VLM layer, not the live demo model.
