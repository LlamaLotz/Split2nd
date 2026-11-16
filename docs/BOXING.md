# Boxing mode: actions, thresholds, and WebSocket contract

Status: **backend + `/boxing` display complete; laptop-camera validation pending.**
The current demo rig is the **laptop integrated webcam + laptop screen + Arduino/ultrasonic**. The controller page is the display. No phone client is required. `VISION=local` is the real path; `VISION=fake` is the automated/demo fallback.

This is a rule-based **gross-action** classifier built on MediaPipe pose landmarks. It is not a trained punch classifier and it does not measure punch force. See [Limits and cue reliability](#limits-and-cue-reliability).

---

## 1. What the drill does

A boxing set is **10 cues**. Each cue:

1. The backend sends `schedule` to the controller page with a cue object and a random `delay_ms` of 500–800 ms.
2. The controller paints the cue (the arena UI) and sends `painted` at the paint time.
3. The backend anchors `t0` on the paint ack and sends `STIM` to the Arduino. The laptop webcam is already running.
4. The athlete performs the cue. Camera frames land in the vision sample stream with **backend `perf_counter` timestamps**.
5. At `t0 + window_ms` (1.5 s), the backend grades the fastest wrist/head movement after GO (a 0.6 s window centred on its peak), finds its onset, scores it, stores it, and broadcasts `result`. A block is graded on the last 0.6 s of the response.
6. The next cue is armed automatically after the result dwell. After cue 10, `session_end` carries the report, stars, chest, mascot, and the one evidence-backed tip for the session.

The racing mode is unchanged. Boxing does not use the encoder or buttons; the real firmware does not have them.

### Pose backend

The primary path is the **MediaPipe Tasks Pose Landmarker**:

- model: `models/pose_landmarker_lite.task` (checked in; no first-use download)
- API: `mediapipe.tasks.python.vision.PoseLandmarker`
- mode: `RunningMode.VIDEO`, one pose, 0.5 detection/tracking confidence
- created **once** in the vision processing thread, not per frame
- `detect_for_video(mp_image, timestamp_ms)` with a strictly increasing millisecond timestamp derived from the capture/perf time
- output: `result.pose_landmarks[0]` and `result.pose_world_landmarks[0]`

A legacy `mp.solutions.pose` adapter is attempted **only if Tasks creation fails**, and frame-difference motion is the final fallback. `GET /api/health` exposes `vision.pose_backend` (`tasks` | `legacy` | `motion` | `pending` | `fake`), `vision.pose_model_exists`, and `vision.pose_error`.

Smoke test:

```bash
python scripts/check_pose_landmarker.py
# POSE_LANDMARKER_OK model=.../models/pose_landmarker_lite.task backend=tasks
```

### Real ultrasonic Z (COM6)

The HC-SR04 is a real depth channel, not a pose prediction. The engine polls
`DIST` while idle and records the real `HIT <cm>` at the gate during the rep;
both feed a timestamped history. Each pose frame is matched to the nearest
fresh reading and the classifier computes `sensor_drop_cm`. That real drop is
the **primary** straight-punch condition; monocular pose world-z is only the
fallback when the sensor has no fresh reading. Hooks and uppercuts are still
classified from X/Y and elbow geometry. The live page shows the raw Z and the
Z drop for every cue.

Run the laptop rig with:

```bash
python scripts/run.py --no-tls --vision local --rig serial --rig-port COM6
```

The WSL unit-test environment does not need MediaPipe; all classifier tests use synthetic landmarks. On the Windows demo laptop, run the smoke test once before the set.

---

## 2. Action catalog

`backend/boxing.py: ACTION_CATALOG`

| id | label | catalog hand | expected pattern | family |
|---|---|---|---|---|
| `jab` | Jab | `lead` | straight | offense |
| `cross` | Cross | `rear` | straight | offense |
| `hook_l` | Left hook | `left` | hook | offense |
| `hook_r` | Right hook | `right` | hook | offense |
| `uppercut_l` | Left uppercut | `left` | uppercut | offense |
| `uppercut_r` | Right uppercut | `right` | uppercut | offense |
| `slip_l` | Slip left | `left` | slip | defense |
| `slip_r` | Slip right | `right` | slip | defense |
| `block` | Block | `both` | block | defense |

Stance resolves only `lead`/`rear`:

| stance | lead | rear |
|---|---|---|
| `orthodox` | left | right |
| `southpaw` | right | left |

The API cue object is:

```json
{
  "id": "jab",
  "action": "jab",
  "label": "Jab",
  "hand": "lead",
  "hand_side": "left",
  "pattern": "straight",
  "family": "offense",
  "cue": "Jab - lead hand, straight out, elbow extends",
  "stance": "orthodox"
}
```

`hand` is the catalog's relative side; `hand_side` is the resolved physical side. `slip_l`/`slip_r` use `hand` as the head direction, not a punch hand. `block` uses `both`.

### Default set (10 cues)

```python
["jab", "cross", "hook_l", "slip_r", "uppercut_l",
 "block", "hook_r", "slip_l", "uppercut_r", "cross"]
```

All nine catalog actions are represented. No cue is excluded on synthetic evidence. The Windows-camera run is the gate; if a cue is unreliable on the real rig, remove it from `DEFAULT_SET` and leave the rest. Do not add a cue to the default set just because it classifies in synthetic landmarks.

---

## 3. Features

For every frame in the response window, `backend/boxing.py` parses the image landmarks `(x, y, z, visibility)` and the world landmarks `(x, y, z)`. World landmarks are preferred when at least 60% of frames have them; otherwise image landmarks are used.

For scoring, `dominant_movement()` first finds the fastest moving part after GO (left wrist, right wrist, or head) and takes a 0.6 s window centred on its peak speed. Pre-GO frames are used only for the onset baseline. This keeps the previous rep, a slow guard raise, and a post-punch reset out of the current classification. The cue's own hand is preferred when it is moving at least 60% as fast as the fastest part, so a guard-hand twitch cannot steal a rear-hand uppercut; a genuinely wrong-hand punch still wins when the called hand barely moves. The block cue is graded on the last 0.6 s of the response instead, because it is a held posture.

Coordinates are translated to the shoulder centre and divided by the **median shoulder width** in the same coordinate system. That makes the thresholds stance-, distance-, and camera-scale-independent.

Per hand (left and right):

- wrist relative `x/y/z`, start position, end position
- wrist velocity and peak speed
- total travel and the frame of maximum travel
- depth drop (`start_z - min_z`)
- rise (`start_y - min_y`)
- cross-body travel (`across`, signed toward the opposite side of the body)
- elbow angle (shoulder–elbow–wrist) at the start, at deepest `z`, at maximum travel, at highest `y`, and min/max
- wrist path length

Global:

- head (nose) relative `x/y/z`, head displacement, head path, head peak speed
- wrist distance to the head at the head's peak displacement
- shoulder rotation from the left/right shoulder `z` difference
- block fractions: both wrists above the shoulders, both wrists near the head, both wrists above the elbows, both wrists at/above the nose line
- block peak wrist speed and combined wrist path

A window with fewer than `min_frames = 6` pose frames is `unknown`. At 10 fps, a 1.5 s window still carries about 14 frames plus the pre-roll.

---

## 4. Rules and confidence

Every candidate has a **primary condition**. A candidate whose primary condition is false is discarded, even if secondary conditions happen to be true. Confidence is the fraction of that candidate's conditions that were met; below `0.25` is `unknown`.

| action | primary condition | other conditions |
|---|---|---|
| `jab` / `cross` | depth drop ≥ 0.30 shoulder-widths, or elbow extension ≥ 145° + travel ≥ 0.20 when depth is unreliable | elbow max ≥ 145°, travel ≥ 0.22, forward-hand dominance, forward axis dominance |
| `hook_l` / `hook_r` | cross-body x travel ≥ 0.14 **and** horizontal travel dominates the rise | elbow at max travel 70–150°, vertical change ≤ 0.50, travel ≥ 0.22, cross-body dominance |
| `uppercut_l` / `uppercut_r` | start below shoulder ≥ −0.50 **and** rise ≥ 0.16 **and** rise dominates cross-body travel | elbow at highest point ≤ 150°, travel ≥ 0.22, rise dominance |
| `slip_l` / `slip_r` | head x displacement ≥ 0.35 | lateral-dominant head movement, wrists within 1.10 of the head, head-led motion |
| `block` | both wrists above the shoulders ≥ 70% **and** both near the head ≥ 70% **and** wrist speed ≤ 3.0 | above elbows, above nose line, head x/y net displacement ≤ 0.20/0.25, wrist path ≤ 6.0 |

The exact tunable block (the only place to change thresholds):

```python
THRESHOLDS = {
    "min_frames": 6,
    "min_visibility": 0.25,
    "straight_depth_drop": 0.30,
    "straight_elbow_extended": 145.0,
    "straight_travel": 0.22,
    "straight_depth_lead": 0.10,
    "straight_forward_ratio": 0.50,
    "hook_cross_travel": 0.14,
    "hook_elbow_min": 70.0,
    "hook_elbow_max": 150.0,
    "hook_vertical_max": 0.50,
    "hook_travel": 0.22,
    "hook_cross_lead": 0.05,
    "uppercut_start_below": -0.50,
    "uppercut_rise": 0.16,
    "uppercut_elbow_max": 150.0,
    "uppercut_travel": 0.22,
    "uppercut_rise_lead": 0.10,
    "slip_head_dx": 0.35,
    "slip_head_dy_ratio": 0.80,
    "slip_wrist_face": 1.10,
    "slip_head_path_min": 0.30,
    "slip_head_share": 0.50,
    "block_wrist_above": 0.10,
    "block_wrist_face": 0.90,
    "block_max_wrist_speed": 3.00,
    "block_max_wrist_path": 6.00,
    "block_head_dx": 0.20,
    "block_head_dy": 0.25,
    "block_high_fraction": 0.70,
    "onset_k": 3.0,
    "onset_need": 3,
    "onset_floor": 0.50,
    "onset_baseline_s": 0.35,
    "exec_speed_ratio": 0.20,
    "exec_floor": 0.30,
    "sensor_straight_drop_cm": 4.0,
    "sensor_min_drop_cm": 2.0,
}
```

### Onset and reaction time

`onset_ms(frames, cue, stance, t0)`:

1. Pick the expected body part: the resolved hand for a punch, the head for a slip, both hands for a block.
2. Take the 0.35 s of frames before `t0` as the baseline. If there are fewer than 3, use the first 3 frames.
3. Threshold = `max(mean + 3*std, 0.50, mean*2.2)`.
4. Smooth the speed with a centred 3-frame average to suppress pose jitter. The timestamp returned is still the first frame of the run.
5. Return the first run of **3 consecutive** frames above threshold, in milliseconds after `t0`.

At 10 fps, that is deliberately conservative: 3 frames is 300 ms of movement. It costs some latency, but avoids turning pose jitter into a false 80 ms reaction.

`execution_ms` is the first-to-last frame above 20% of the action's peak speed. It is not the reaction time; it is how long the action itself took.

### Block special case

A block is a held posture, not a single movement. At 10–12 fps the small guard raise may produce no measurable onset. If the cue is `block` and the pose classifies as a confident block, the cue is **correct** with `reaction_ms: null`, `grade: "Y"`, and zero speed XP. A held guard is a valid block; a still athlete with hands down is still `unknown`/miss. Do not interpret `reaction_ms: null` on a block as a camera failure without checking the pose window.

### Grade and rewards

- Grade: correct and RT ≤ 380 ms → `G`; ≤ 500 ms → `Y`; otherwise `R`. A correct block with no onset is `Y`. Wrong/miss is `R`.
- XP (`backend/xp.py: xp_for_boxing`):
  - base 8
  - speed: `clamp(round((700 - rt) / 30), 0, 12)`
  - combo multiplier: `min(2.0, 1 + 0.1 * combo)`
  - session accuracy bonus: `round(30 * accuracy)`
  - wrong/miss: 0
  - `combo` in the XP payload is the bonus points from the multiplier; `multiplier` is also included.
- Combo resets to 0 on wrong/miss. The session accuracy is `correct / cues_attempted` after the current cue.

---

## 5. WebSocket contract (frozen)

`/ws?role=controller&player_id=<id>` is the display on the current laptop rig. A `player` client still works for racing but is not required for boxing. The server selects the controller when no phone is connected.

### Start

Client → server:

```json
{
  "cmd": "start",
  "player_id": "p_1234abcd",
  "mode": "boxing",
  "stance": "orthodox",
  "ghost_session_id": null
}
```

`mode` defaults to `racing`. `stance` defaults to the last stance, then `orthodox`. `ghost_session_id` is optional; an unknown or non-boxing session is rejected with `fault`. Boxing with a room code is rejected (`boxing is a solo drill`).

### `schedule`

Server → controller (the display client only). Racing schedules are unchanged; boxing schedules add `mode`, `cue`, `window_ms`, `progress`, and the ghost fields:

```json
{
  "type": "schedule",
  "mode": "boxing",
  "stance": "orthodox",
  "delay_ms": 630,
  "lights": 0,
  "light_interval_ms": 0,
  "player_id": "p_1234abcd",
  "player_name": "Athlete",
  "rep": 1,
  "room": null,
  "source": "local",
  "display": "controller",
  "cue": {
    "id": "jab",
    "action": "jab",
    "label": "Jab",
    "hand": "lead",
    "hand_side": "left",
    "pattern": "straight",
    "family": "offense",
    "cue": "Jab - lead hand, straight out, elbow extends",
    "stance": "orthodox"
  },
  "window_ms": 1500,
  "progress": {"done": 0, "total": 10, "correct": 0, "accuracy": 0.0},
  "ghost_session_id": null,
  "ghost": []
}
```

- `source` is `local` on the laptop rig, `fake` for automated runs, and `remote` only for the legacy phone path.
- `display` is `controller` on the current rig.
- The controller must paint and send `{"type":"painted","ts":<performance.now>,"clock":"<client clock>"}` after `delay_ms`. The backend ignores the client `ts` for a local camera; `t0` is the backend receive time and the local frames are on the backend `perf_counter` clock.
- `rep` is 1..10. `progress.done` is completed cues before this one.

### `result`

Broadcast after every cue:

```json
{
  "type": "result",
  "mode": "boxing",
  "player_id": "p_1234abcd",
  "player": {"id": "p_1234abcd", "name": "Athlete", "level": 2, "xp": 220},
  "session_id": "s_1234abcd",
  "cue": "jab",
  "cue_label": "Jab",
  "cue_text": "Jab - lead hand, straight out, elbow extends",
  "expected_action": "jab",
  "detected_action": "jab",
  "expected": "jab",
  "detected": "jab",
  "hand": "left",
  "confidence": 1.0,
  "correct": true,
  "outcome": "correct",
  "grade": "G",
  "reaction_ms": 317,
  "execution_ms": 133,
  "rt_ms": 317,
  "false_start": false,
  "miss": false,
  "hit_ms": null,
  "hit_cm": null,
  "hit_delta_ms": null,
  "combo": 1,
  "xp": {
    "base": 8,
    "speed": 12,
    "combo": 2,
    "accuracy": 30,
    "multiplier": 1.1,
    "total": 52,
    "reason": "clean cue"
  },
  "tip": null,
  "progress": {"done": 1, "total": 10, "correct": 1, "accuracy": 1.0},
  "stats": {"n": 1, "cues": 1, "...": "same object as /api/players/{id}/boxing stats"},
  "session_stats": {
    "n": 1, "cues": 1, "correct": 1, "wrong": 0, "miss": 0,
    "accuracy": 1.0, "median_rt_ms": 317, "best_rt_ms": 317,
    "max_combo": 1, "rts": [317]
  },
  "ghost_ms": null,
  "ghost_delta": null,
  "rep": 1
}
```

Field notes:

- `expected_action`/`detected_action` are canonical; `expected`/`detected` are aliases for UI convenience.
- `detected_action` is `null` when the classifier is unknown.
- `outcome` is `correct` | `wrong` | `miss`.
- `reaction_ms` can be `null` for a correct block (see above). `execution_ms` can be `null` if no movement passed the execution threshold.
- `grade` is `G` | `Y` | `R`. A miss/wrong is `R`. `false_start` is always false in boxing; `miss` mirrors `outcome == "miss"`.
- `hit_ms`/`hit_cm` are the Arduino ultrasonic cross-check when the gate tripped during the response window; `hit_delta_ms = hit_ms - reaction_ms` when both exist. They are never used to score the action.
- `tip` is `null` on cues 1–9 and carries the single session tip on cue 10 (and in `session_end`).
- `stats` is the full boxing profile. `session_stats` is the current session slice.

### `session_end`

Broadcast after cue 10, then the engine returns to `idle`:

```json
{
  "type": "session_end",
  "mode": "boxing",
  "session_id": "s_1234abcd",
  "player_id": "p_1234abcd",
  "player": {"id": "p_1234abcd", "name": "Athlete", "level": 2, "xp": 830},
  "report": {
    "cues": 10,
    "correct": 10,
    "wrong": 0,
    "miss": 0,
    "accuracy": 1.0,
    "median_rt_ms": 317,
    "best_rt_ms": 317,
    "mean_execution_ms": 142,
    "max_combo": 10,
    "combo_breaks": 0,
    "guard_drops": 0,
    "xp": 610,
    "by_action": {
      "jab": {"cues": 2, "correct": 2, "wrong": 0, "miss": 0,
              "accuracy": 1.0, "median_rt_ms": 317}
    },
    "fatigue": {
      "first_third_rt_ms": 317,
      "last_third_rt_ms": 320,
      "pct": 0.9
    },
    "trace": [
      {"index": 1, "cue": "jab", "expected": "jab", "detected": "jab",
       "correct": true, "outcome": "correct", "rt_ms": 317,
       "reaction_ms": 317, "execution_ms": 133, "confidence": 1.0,
       "combo": 1, "hand": "left"}
    ]
  },
  "stars": 3,
  "chest": {
    "tier": "gold",
    "title": "Golden guard",
    "detail": "10/10 cues correct"
  },
  "mascot": {
    "mood": "happy",
    "line": "Hands up, eyes open. That is a set."
  },
  "tip": {
    "headline": "Accuracy first, then speed",
    "body": "...",
    "refs": [{"id": "fitts", "title": "...", "url": "https://doi.org/..."}],
    "disclaimer": "Single-camera gross-action label. ..."
  }
}
```

- Stars: 3 at accuracy ≥ 0.9, 2 at ≥ 0.7, 1 at ≥ 0.5, else 0.
- Chest tiers: `gold` / `silver` / `bronze` / `none`.
- Mascot moods: `happy` / `neutral` / `tired`. The mood is `tired` when the last-third fatigue is > 10%, otherwise based on stars.
- `report.trace` is the same per-cue trace as `GET /api/sessions/{id}/trace`.

### `state`

The existing state message gains boxing fields; racing keeps its old fields and gets `mode: "racing"`, `cue: null`, `progress: null`.

```json
{
  "type": "state",
  "phase": "arm|wait|response|result|idle",
  "msg": "Guard up - Jab",
  "mode": "boxing",
  "stance": "orthodox",
  "cue": {"id": "jab", "...": "the cue object, or null"},
  "progress": {"done": 0, "total": 10, "correct": 0, "accuracy": 0.0},
  "session_id": "s_1234abcd",
  "ghost_session_id": null,
  "player_id": "p_1234abcd",
  "player": {"id": "p_1234abcd", "name": "Athlete"},
  "source": "local",
  "display": "controller",
  "gate_cm": 30,
  "room": null,
  "result": {"type": "result", "...": "last result or null"},
  "auto": false,
  "error": null
}
```

### `fault`

Unchanged shape: `{"type":"fault","message":"Camera is not streaming"}`. Local camera faults appear when no frames arrive for 1.5 s during arm. Check `GET /api/health` → `vision.mode`, `vision.fps`, `vision.pose_ok`.

---

## 6. REST

### `GET /api/boxing/catalog?stance=orthodox`

Returns `boxing.catalog()`: stance table, resolved actions, default set, window/delay/grade values, confidence floor, and the full `thresholds` object.

### `GET /api/players/{id}/boxing`

```json
{"player": {"id": "p_...", "name": "Athlete", "...": "..."},
 "stats": {
   "player_id": "p_...",
   "n": 10, "cues": 10, "correct": 8, "wrong": 1, "miss": 1,
   "accuracy": 0.8,
   "median_rt_ms": 330, "best_rt_ms": 290, "mean_execution_ms": 210,
   "max_combo": 5, "combo_breaks": 2, "guard_drops": 1,
   "lead_accuracy": 0.9, "rear_accuracy": 0.7, "lead_bias": 0.2,
   "bias": "lead",
   "actions": {
     "jab": {"cues": 2, "correct": 2, "wrong": 0, "miss": 0,
             "accuracy": 1.0, "median_rt_ms": 310}
   },
   "fatigue": {"first_third_rt_ms": 310, "last_third_rt_ms": 350, "pct": 12.9},
   "best_session": {"session_id": "s_...", "started_at": "...", "cues": 10,
                    "correct": 8, "accuracy": 0.8, "median_rt_ms": 330, "xp": 410},
   "recent_sessions": [{"session_id": "s_...", "trace": ["..."]}]
 }}
```

Stats definitions:

- `median_rt_ms` uses every measured reaction time (correct and wrong; miss has no RT).
- Per-action accuracy is `correct / cues` for that expected action.
- Lead/rear bias uses punch actions only (jab/cross/hook/uppercut) and each rep's stored stance. `lead_bias = lead_accuracy - rear_accuracy`; `bias` is `lead`, `rear`, or `balanced`.
- `guard_drops` counts defensive cues (`slip_l`, `slip_r`, `block`) that were wrong or missed.
- `combo_breaks` counts a correct→not-correct transition.
- Fatigue is first-third vs last-third measured reaction time; `pct = (last - first) / first * 100`.
- `best_session` is highest accuracy, ties by lower median RT.
- `recent_sessions` holds up to 5 session trace objects.

### `GET /api/leaderboard?mode=boxing`

Sorted by **accuracy descending, then median RT ascending**. A boxing row does **not** have `best_ms`:

```json
[{"player_id": "p_...", "name": "Athlete", "mode": "boxing",
  "accuracy": 0.9, "correct": 9, "cues": 10, "median_rt_ms": 310,
  "reps": 10, "xp": 1349, "streak": 12, "level": 5, "demo": true}]
```

### `GET /api/ghosts?player_id=<id>&mode=boxing[&session_id=<id>][&limit=5]`

Returns `{"player_id","mode","ghosts":[<session trace>, ...]}`. Without `session_id`, the most recent boxing sessions are returned, newest first, up to `limit`. With `session_id`, only that session is returned (if it belongs to the player). This is the ghost/leaderboard hook; there is no social graph.

### `GET /api/sessions/{id}/trace`

Returns one session trace:

```json
{
  "session_id": "s_...",
  "player_id": "p_...",
  "player_name": "Athlete",
  "mode": "boxing",
  "started_at": "...",
  "ended_at": "...",
  "trace": [
    {"index": 1, "cue": "jab", "expected": "jab", "detected": "jab",
     "correct": true, "outcome": "correct", "rt_ms": 317,
     "reaction_ms": 317, "execution_ms": 133, "confidence": 1.0,
     "combo": 1, "hand": "left"}
  ],
  "summary": {"cues": 10, "correct": 8, "accuracy": 0.8,
              "median_rt_ms": 330, "best_rt_ms": 290, "xp": 410}
}
```

Racing sessions also return a trace; their cue is `racing`, their detected value is `hit`/`miss`/`false_start`, and their RT is the racing RT.

---

## 7. Fake mode

```bash
VISION=fake RIG=fake python -m uvicorn backend.main:app --port 8460
```

`VisionEngine.simulate_boxing(cue_id, t0, stance)` appends 30 fps synthetic landmark frames from `t0 - 0.35` to `t0 + 1.2`. The frames carry both image and world landmarks; the synthetic action starts 300 ms after `t0`. The fake drill is deterministic enough for tests and for a UI demo with no camera.

`VISION=local RIG=auto` is the real rig. The camera is the laptop webcam; the controller page is the display. The backend does not need a phone connection. If the local camera fails to open, `VisionEngine` falls back to fake and `GET /api/health` shows `vision.mode: "fake"`; check that before blaming a cue.

---

## 8. Limits and cue reliability

**Single-camera gross action, not a punch meter.** A 2D/3D pose estimate can say "the wrist extended forward while the elbow opened" but it cannot measure punch force, impact, or head acceleration. MediaPipe world landmarks are model estimates, not depth-sensor measurements. Occlusion (hand behind the head, hand outside the frame), loose clothing, low light, and a camera above/below chest height all change the geometry. Cite this in the pitch: Manoharan et al. 2025 found video-only punch classification degraded by motion blur and occlusion and needed synchronized IMU for ~92% classification; this build is coarser still — a rule-based gross-action label from one camera.

**Frame rate.** The laptop camera may run at 10–30 fps. The window is 1.5 s, `min_frames` is 6, and onset needs 3 consecutive frames. At 10 fps the reaction time is quantized to ~100 ms and the block may have no measurable onset (handled as a correct block with `reaction_ms: null`). Do not compare 10 fps and 30 fps reaction times as if they were the same measurement.

**Mirroring and stance.** The classifier derives the athlete's spatial left/right from the shoulder ordering in the pose frame. Do not horizontally flip the frame before MediaPipe; a flipped image can swap the model's left/right labels and make jab/cross and slip_l/slip_r disagree with the cue. The laptop webcam is used unmirrored for this reason. If the camera is mounted behind the athlete or the athlete faces away, the shoulder-order rule still handles the spatial direction, but the pose labels themselves may not; validate before the demo.

**Cue reliability on the current synthetic evidence:**

- Reliable in synthetic tests at 10, 12, 15, 20 and 30 fps: `jab`, `cross`, `hook_l`, `hook_r`, `uppercut_l`, `uppercut_r`, `slip_l`, `slip_r`, `block`.
- Most sensitive to real-camera noise: **hooks** (the elbow-angle window and the cross-body x travel pull in opposite directions), **uppercuts** (the start-below-shoulder condition depends on the guard height), **slips** (the head must be visible and the hands must stay near the face), and **block** (both hands must be in frame and high).
- Most robust: **jab** and **cross**, because the depth drop and elbow extension are the largest signals.
- No cue is removed from the default set on synthetic evidence. If the Windows camera makes a cue unreliable, remove that cue from `DEFAULT_SET` in `backend/boxing.py` and say so in the demo notes; keep the rest. The master will report which cue, if any, fails on the real camera.

**Hardware.** The real firmware has no encoder and no buttons. The backend sends `DEMO 0` after `READY`, parses `VER`, `OK`, `ERR`, `DIST`, `DEMO`, `I2C`, `TIMEOUT`, and treats `HIT <ms> <cm>` as the ultrasonic cross-check. The OLED `SHOW` command accepts only `G/Y/R/F`; a boxing miss grade `M` is mapped to `R` in `backend/rig.py`. The gate is quantized by the 25 ms ping interval, so `hit_delta_ms` is a sanity check, not a scoring input.

---

## 9. Evidence used by the boxing tips

`backend/evidence.py: boxing_feedback` picks exactly one tip per session:

| Trigger | Tip focus | Source |
|---|---|---|
| cross slower/less accurate than the set | rear-foot drive and elbow extension | Dinu & Louis 2020, *Front Sports Act Living*, [PMC7739747](https://pmc.ncbi.nlm.nih.gov/articles/PMC7739747/) |
| hook/uppercut accuracy < 0.75 | shoulder/trunk rotation, not the arm | Dinu & Louis 2020, [PMC7739747](https://pmc.ncbi.nlm.nih.gov/articles/PMC7739747/) |
| last-third RT > first third by > 8% | fatigue; stop the set | Stewart et al. 2025, *Bioengineering*, [PMC12729554](https://pmc.ncbi.nlm.nih.gov/articles/PMC12729554/) |
| median RT > 500 ms | sleep, choice load, anticipation | Lim & Dinges 2010 [PMC3290659](https://pmc.ncbi.nlm.nih.gov/articles/PMC3290659/); Hick 1952; Zhang et al. 2022 [PMC9650920](https://pmc.ncbi.nlm.nih.gov/articles/PMC9650920/) |
| ≥ 2 guard drops | keep the hands high, scan the lead shoulder | Zhang et al. 2022, [PMC9650920](https://pmc.ncbi.nlm.nih.gov/articles/PMC9650920/) |
| ≥ 2 combo breaks or max combo < 2 | chunk the combination | Hick 1952; Welford 1980 |
| default | accuracy before speed; compare within athlete | Fitts 1954; BlazePod reliability study |

The boxing disclaimer says plainly that the action label is a single-camera geometry estimate, not punch force or a head-impact measurement. The tip refs are the real URLs above; do not add a citation that is not in `REFS`.
