"""Rule-based gross-action boxing classifier.

This is deliberately *not* a trained punch classifier.  It takes a short
window of MediaPipe-style pose landmarks, normalises everything by shoulder
width, and scores a handful of hand-written geometric rules.  The output is
a gross action label (jab / cross / hook / uppercut / slip / block) with a
confidence equal to the fraction of that action's conditions that were met.

Everything here is pure Python and works on plain dicts, so tests do not
need MediaPipe, OpenCV or a camera.

Frame shape accepted by :func:`classify_window` and :func:`onset_ms`::

    {
      "t": 12.345,                       # seconds, same clock as t0
      "image": [(x, y, z, visibility), ... 33],   # normalised image coords
      "world": [(x, y, z), ... 33],                # metric world coords
    }

Either landmark list may be omitted.  World landmarks are preferred when
present; image landmarks are the fallback.  Landmarks may be tuples/lists or
mapping objects with x/y/z/visibility keys.
"""
from __future__ import annotations

import math
from typing import Any, Iterable, Sequence

# ----------------------------------------------------------------------
# Action catalog
# ----------------------------------------------------------------------
# `hand` is intentionally relative for the straight punches: the lead hand
# is the jab, the rear hand is the cross.  Stance resolves it.  Hooks,
# uppercuts and slips name a physical side/direction, and block is both.
ACTION_CATALOG: dict[str, dict[str, Any]] = {
    "jab": {
        "id": "jab",
        "label": "Jab",
        "hand": "lead",
        "pattern": "straight",
        "family": "offense",
        "cue": "Jab - lead hand, straight out, elbow extends",
    },
    "cross": {
        "id": "cross",
        "label": "Cross",
        "hand": "rear",
        "pattern": "straight",
        "family": "offense",
        "cue": "Cross - rear hand, straight through, hip and rear foot",
    },
    "hook_l": {
        "id": "hook_l",
        "label": "Left hook",
        "hand": "left",
        "pattern": "hook",
        "family": "offense",
        "cue": "Left hook - turn the shoulder, horizontal elbow",
    },
    "hook_r": {
        "id": "hook_r",
        "label": "Right hook",
        "hand": "right",
        "pattern": "hook",
        "family": "offense",
        "cue": "Right hook - turn the shoulder, horizontal elbow",
    },
    "uppercut_l": {
        "id": "uppercut_l",
        "label": "Left uppercut",
        "hand": "left",
        "pattern": "uppercut",
        "family": "offense",
        "cue": "Left uppercut - drop then drive up, elbow stays bent",
    },
    "uppercut_r": {
        "id": "uppercut_r",
        "label": "Right uppercut",
        "hand": "right",
        "pattern": "uppercut",
        "family": "offense",
        "cue": "Right uppercut - drop then drive up, elbow stays bent",
    },
    "slip_l": {
        "id": "slip_l",
        "label": "Slip left",
        "hand": "left",
        "pattern": "slip",
        "family": "defense",
        "cue": "Slip left - head off the centre line, hands by the face",
    },
    "slip_r": {
        "id": "slip_r",
        "label": "Slip right",
        "hand": "right",
        "pattern": "slip",
        "family": "defense",
        "cue": "Slip right - head off the centre line, hands by the face",
    },
    "block": {
        "id": "block",
        "label": "Block",
        "hand": "both",
        "pattern": "block",
        "family": "defense",
        "cue": "Block - high guard, both wrists above the shoulders",
    },
}

STANCES: dict[str, dict[str, str]] = {
    "orthodox": {"lead": "left", "rear": "right"},
    "southpaw": {"lead": "right", "rear": "left"},
}

# 10 cues, mixed offense/defense.  Keep this list in sync with docs/BOXING.md.
DEFAULT_SET: list[str] = [
    "jab",
    "cross",
    "hook_l",
    "slip_r",
    "uppercut_l",
    "block",
    "hook_r",
    "slip_l",
    "uppercut_r",
    "cross",
]

DEFENSIVE = {"slip_l", "slip_r", "block"}
PUNCHES = {"jab", "cross", "hook_l", "hook_r", "uppercut_l", "uppercut_r"}

WINDOW_MS = 1400
DELAY_MIN_MS = 500
DELAY_MAX_MS = 800
GREEN_MS = 380
YELLOW_MS = 500
CONFIDENCE_MIN = 0.45

# ----------------------------------------------------------------------
# Every tunable threshold lives here.  Distances are in shoulder-widths,
# speeds in shoulder-widths per second, angles in degrees.
# ----------------------------------------------------------------------
THRESHOLDS: dict[str, float] = {
    "min_frames": 6,
    "min_visibility": 0.25,
    # straight punches: depth (z) decreases, elbow extends
    "straight_depth_drop": 0.45,
    "straight_elbow_extended": 150.0,
    "straight_travel": 0.50,
    "straight_depth_lead": 0.10,
    "straight_forward_ratio": 0.50,
    # hooks: x travels across the body, elbow stays bent, little y change
    "hook_cross_travel": 0.30,
    "hook_elbow_min": 70.0,
    "hook_elbow_max": 120.0,
    "hook_vertical_max": 0.35,
    "hook_travel": 0.40,
    "hook_cross_lead": 0.05,
    # uppercuts: start below the shoulder, rise, elbow stays flexed
    "uppercut_start_below": 0.10,
    "uppercut_rise": 0.35,
    "uppercut_elbow_max": 135.0,
    "uppercut_travel": 0.40,
    "uppercut_rise_lead": 0.10,
    # slips: head leaves the line while the hands stay by the face
    "slip_head_dx": 0.35,
    "slip_head_dy_ratio": 0.80,
    "slip_wrist_face": 1.10,
    "slip_head_path_min": 0.30,
    "slip_head_share": 0.50,
    # block: both wrists high, near the head, and not travelling
    "block_wrist_above": 0.10,
    "block_wrist_face": 0.90,
    "block_max_wrist_speed": 4.00,
    "block_max_wrist_path": 6.00,
    "block_head_dx": 0.20,
    "block_head_dy": 0.25,
    "block_high_fraction": 0.70,
    # onset / reaction time
    "onset_k": 3.0,
    "onset_need": 3,
    "onset_floor": 0.50,
    "onset_baseline_s": 0.35,
    # execution time window
    "exec_speed_ratio": 0.20,
    "exec_floor": 0.30,
    # Real HC-SR04 Z: a straight punch must close distance toward the laptop.
    # Used as the primary straight condition whenever the sensor is fresh; the
    # monocular pose depth is only the fallback when no sensor reading exists.
    "sensor_straight_drop_cm": 6.0,
    "sensor_min_drop_cm": 2.0,
}

# MediaPipe Pose landmark indexes.
NOSE = 0
L_EAR, R_EAR = 7, 8
L_SHOULDER, R_SHOULDER = 11, 12
L_ELBOW, R_ELBOW = 13, 14
L_WRIST, R_WRIST = 15, 16
L_HIP, R_HIP = 23, 24
KEY_LANDMARKS = (NOSE, L_SHOULDER, R_SHOULDER, L_ELBOW, R_ELBOW,
                 L_WRIST, R_WRIST)

# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------
def _as_points(raw: Any) -> list[tuple[float, float, float, float]] | None:
    """Normalise a landmark list to 33 (x, y, z, visibility) tuples."""
    if raw is None:
        return None
    try:
        items = list(raw)
    except TypeError:
        return None
    if len(items) < 33:
        return None
    out: list[tuple[float, float, float, float]] = []
    for item in items[:33]:
        try:
            if isinstance(item, dict):
                x = float(item["x"])
                y = float(item["y"])
                z = float(item.get("z", 0.0))
                vis = float(item.get("visibility", item.get("vis", 1.0)))
            else:
                x = float(item[0])
                y = float(item[1])
                z = float(item[2]) if len(item) > 2 else 0.0
                vis = float(item[3]) if len(item) > 3 else 1.0
        except (KeyError, IndexError, TypeError, ValueError):
            return None
        out.append((x, y, z, vis))
    return out


def _mid(a: Sequence[float], b: Sequence[float]) -> tuple[float, float, float]:
    return ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0, (a[2] + b[2]) / 2.0)


def _dist(a: Sequence[float], b: Sequence[float]) -> float:
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)


def _norm(v: Sequence[float]) -> float:
    return math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)


def _angle_3d(a: Sequence[float], b: Sequence[float], c: Sequence[float]) -> float | None:
    """Angle at b between a-b and c-b, in degrees."""
    u = (a[0] - b[0], a[1] - b[1], a[2] - b[2])
    v = (c[0] - b[0], c[1] - b[1], c[2] - b[2])
    nu, nv = _norm(u), _norm(v)
    if nu < 1e-9 or nv < 1e-9:
        return None
    cos = max(-1.0, min(1.0, (u[0] * v[0] + u[1] * v[1] + u[2] * v[2]) / (nu * nv)))
    return math.degrees(math.acos(cos))


def _median(values: Iterable[float]) -> float | None:
    vals = sorted(float(v) for v in values)
    if not vals:
        return None
    mid = len(vals) // 2
    if len(vals) % 2:
        return vals[mid]
    return (vals[mid - 1] + vals[mid]) / 2.0


def _mean_point(points: Sequence[Sequence[float]]) -> tuple[float, float, float]:
    n = max(1, len(points))
    return (sum(p[0] for p in points) / n,
            sum(p[1] for p in points) / n,
            sum(p[2] for p in points) / n)


def resolve_hand(action_or_hand: str, stance: str = "orthodox") -> str:
    """Resolve 'lead'/'rear' for a stance; pass through left/right/both."""
    stance = stance if stance in STANCES else "orthodox"
    key = ACTION_CATALOG.get(action_or_hand, {}).get("hand", action_or_hand)
    if key in ("lead", "rear"):
        return STANCES[stance][key]
    return key


def _lead_dir(points: Sequence[tuple[float, float, float, float]]) -> float:
    """+1 when the athlete's left shoulder has the larger x coordinate.

    This makes the slip/cross-body rules independent of camera mirroring and
    of whether the athlete faces toward or away from the camera.
    """
    return 1.0 if points[L_SHOULDER][0] >= points[R_SHOULDER][0] else -1.0


def _head_point(points: Sequence[tuple[float, float, float, float]]) -> tuple[float, float, float]:
    nose = points[NOSE]
    if nose[3] >= THRESHOLDS["min_visibility"]:
        return (nose[0], nose[1], nose[2])
    if points[L_EAR][3] >= THRESHOLDS["min_visibility"] and \
            points[R_EAR][3] >= THRESHOLDS["min_visibility"]:
        return _mid(points[L_EAR], points[R_EAR])
    return (nose[0], nose[1], nose[2])


def _parse_frames(frames: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    parsed: list[dict[str, Any]] = []
    for frame in frames:
        if not isinstance(frame, dict):
            continue
        try:
            t = float(frame.get("t", frame.get("ts", 0.0)))
        except (TypeError, ValueError):
            continue
        image = _as_points(frame.get("image") or frame.get("landmarks")
                           or frame.get("pose"))
        world = _as_points(frame.get("world") or frame.get("world_landmarks"))
        if image is None and world is None:
            continue
        try:
            sensor = frame.get("sensor_z")
            sensor = float(sensor) if sensor is not None else None
            if sensor is not None and sensor < 0:
                sensor = None
        except (TypeError, ValueError):
            sensor = None
        parsed.append({"t": t, "image": image, "world": world, "sensor_z": sensor})
    parsed.sort(key=lambda f: f["t"])
    return parsed


def _extract(frames: Sequence[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return (features, per-frame speed series)."""
    parsed = _parse_frames(frames)
    empty: dict[str, Any] = {
        "n": 0, "source": None, "duration_s": 0.0, "scale": 1.0,
        "left": {}, "right": {}, "head": {}, "shoulder_rotation": None,
        "sensor_drop_cm": None,
    }
    if len(parsed) < 2:
        return empty, []

    world_n = sum(1 for f in parsed if f["world"] is not None)
    use_world = world_n >= max(3, int(round(len(parsed) * 0.6)))

    rows: list[dict[str, Any]] = []
    scales: list[float] = []
    min_vis = THRESHOLDS["min_visibility"]
    for f in parsed:
        pts = f["world"] if use_world else f["image"]
        if pts is None:
            continue
        # If we are using world coordinates, the image visibility still tells
        # us whether the joint was tracked well.
        img = f["image"]
        if img is not None:
            if any(img[i][3] < min_vis for i in KEY_LANDMARKS):
                continue
        elif use_world and pts is None:
            continue
        sw = _dist(pts[L_SHOULDER], pts[R_SHOULDER])
        if sw > 1e-6:
            scales.append(sw)
        rows.append({"t": f["t"], "p": pts, "sensor_z": f.get("sensor_z")})
    if len(rows) < 2:
        return empty, []

    sensor_vals = [r["sensor_z"] for r in rows if r.get("sensor_z") is not None]
    sensor_drop = None
    if (len(sensor_vals) >= 2
            and (max(sensor_vals) - min(sensor_vals)) >= THRESHOLDS["sensor_min_drop_cm"]):
        # A non-dynamic sensor (one stale reading repeated) is not evidence of
        # a straight punch; fall back to monocular world-z in that case.
        sensor_drop = round(max(0.0, float(sensor_vals[0]) - float(min(sensor_vals))), 1)

    scale = _median(scales) or 1.0
    if scale < 1e-6:
        scale = 1.0

    series: list[dict[str, float]] = []
    norm_rows: list[dict[str, Any]] = []
    prev: dict[str, Any] | None = None
    for row in rows:
        pts = row["p"]
        t = row["t"]
        center = _mid(pts[L_SHOULDER], pts[R_SHOULDER])
        head = _head_point(pts)

        def rel(p: Sequence[float]) -> tuple[float, float, float]:
            return ((p[0] - center[0]) / scale,
                    (p[1] - center[1]) / scale,
                    (p[2] - center[2]) / scale)

        lw, rw = rel(pts[L_WRIST]), rel(pts[R_WRIST])
        le, re = rel(pts[L_ELBOW]), rel(pts[R_ELBOW])
        hd = rel(head)
        la = _angle_3d(pts[L_SHOULDER], pts[L_ELBOW], pts[L_WRIST])
        ra = _angle_3d(pts[R_SHOULDER], pts[R_ELBOW], pts[R_WRIST])
        shoulder_rot = (pts[L_SHOULDER][2] - pts[R_SHOULDER][2]) / scale

        l_speed = r_speed = h_speed = 0.0
        if prev is not None:
            dt = t - prev["t"]
            if dt > 1e-5 and dt <= 0.5:
                l_speed = _dist(lw, prev["lw"]) / dt
                r_speed = _dist(rw, prev["rw"]) / dt
                h_speed = _dist(hd, prev["hd"]) / dt

        norm_rows.append({
            "t": t, "lw": lw, "rw": rw, "le": le, "re": re, "hd": hd,
            "la": la, "ra": ra, "shoulder_rot": shoulder_rot,
            "l_speed": l_speed, "r_speed": r_speed, "h_speed": h_speed,
        })
        series.append({"t": t, "left_speed": l_speed,
                       "right_speed": r_speed, "head_speed": h_speed})
        prev = {"t": t, "lw": lw, "rw": rw, "hd": hd}

    first = norm_rows[0]
    lead_dir = _lead_dir(rows[0]["p"])
    start_n = min(3, len(norm_rows))
    start = {k: _mean_point([n[k] for n in norm_rows[:start_n]])
             for k in ("lw", "rw", "hd")}

    def hand_stats(side: str) -> dict[str, Any]:
        key = "lw" if side == "left" else "rw"
        ekey = "le" if side == "left" else "re"
        akey = "la" if side == "left" else "ra"
        skey = "l_speed" if side == "left" else "r_speed"
        start_p = start[key]
        start_angle = first[akey]
        max_travel = -1.0
        travel_frame = norm_rows[0]
        min_z_frame = norm_rows[0]
        min_y_frame = norm_rows[0]
        peak_speed_frame = norm_rows[0]
        for n in norm_rows:
            d = _dist(n[key], start_p)
            if d > max_travel:
                max_travel, travel_frame = d, n
            if n[key][2] < min_z_frame[key][2]:
                min_z_frame = n
            if n[key][1] < min_y_frame[key][1]:
                min_y_frame = n
            if n[skey] > peak_speed_frame[skey]:
                peak_speed_frame = n
        dx = travel_frame[key][0] - start_p[0]
        dy = travel_frame[key][1] - start_p[1]
        dz = travel_frame[key][2] - start_p[2]
        hand_sign = 1.0 if side == "left" else -1.0
        across = -(lead_dir * hand_sign) * dx
        angles = [n[akey] for n in norm_rows if n[akey] is not None]
        return {
            "start": list(start_p),
            "end": list(norm_rows[-1][key]),
            "min_z": min_z_frame[key][2],
            "depth_drop": start_p[2] - min_z_frame[key][2],
            "min_y": min_y_frame[key][1],
            "rise": start_p[1] - min_y_frame[key][1],
            "start_below": start_p[1],
            "dx": dx,
            "dy": dy,
            "dz": dz,
            "across": across,
            "travel": max_travel,
            "elbow_start": start_angle,
            "elbow_at_min_z": min_z_frame[akey],
            "elbow_at_max_travel": travel_frame[akey],
            "elbow_at_min_y": min_y_frame[akey],
            "elbow_at_peak_speed": peak_speed_frame[akey],
            "elbow_min": min(angles) if angles else None,
            "elbow_max": max(angles) if angles else None,
            "peak_speed": peak_speed_frame[skey],
            "peak_speed_t": peak_speed_frame["t"],
            "path": sum(n[skey] * max(0.0, n["t"] - norm_rows[i - 1]["t"])
                        for i, n in enumerate(norm_rows) if i > 0),
        }

    left = hand_stats("left")
    right = hand_stats("right")

    start_head = start["hd"]
    head_travel = -1.0
    head_frame = norm_rows[0]
    head_peak_speed = 0.0
    for n in norm_rows:
        d = _dist(n["hd"], start_head)
        if d > head_travel:
            head_travel, head_frame = d, n
        head_peak_speed = max(head_peak_speed, n["h_speed"])
    head_dx = head_frame["hd"][0] - start_head[0]
    head_dy = head_frame["hd"][1] - start_head[1]
    wrist_face = (
        _dist(head_frame["lw"], head_frame["hd"]) +
        _dist(head_frame["rw"], head_frame["hd"])
    ) / 2.0
    head_path = sum(n["h_speed"] * max(0.0, n["t"] - norm_rows[i - 1]["t"])
                    for i, n in enumerate(norm_rows) if i > 0)
    wrist_path_max = max(left["path"], right["path"])

    above = 0
    near = 0
    above_elbows = 0
    above_nose = 0
    for n in norm_rows:
        l_high = n["lw"][1] < -THRESHOLDS["block_wrist_above"]
        r_high = n["rw"][1] < -THRESHOLDS["block_wrist_above"]
        if l_high and r_high:
            above += 1
        if (_dist(n["lw"], n["hd"]) <= THRESHOLDS["block_wrist_face"] and
                _dist(n["rw"], n["hd"]) <= THRESHOLDS["block_wrist_face"]):
            near += 1
        if n["lw"][1] < n["le"][1] and n["rw"][1] < n["re"][1]:
            above_elbows += 1
        if n["lw"][1] < n["hd"][1] + 0.10 and n["rw"][1] < n["hd"][1] + 0.10:
            above_nose += 1
    count = max(1, len(norm_rows))
    block_speed = max((max(n["l_speed"], n["r_speed"]) for n in norm_rows),
                      default=0.0)
    shoulder_rots = [n["shoulder_rot"] for n in norm_rows]

    features: dict[str, Any] = {
        "n": len(norm_rows),
        "source": "world" if use_world else "image",
        "duration_s": round(norm_rows[-1]["t"] - norm_rows[0]["t"], 3),
        "scale": round(scale, 5),
        "lead_dir": lead_dir,
        "left": left,
        "right": right,
        "head": {
            "start": list(start_head),
            "end": list(norm_rows[-1]["hd"]),
            "dx": head_dx,
            "dy": head_dy,
            "travel": head_travel,
            "path": head_path,
            "peak_speed": head_peak_speed,
            "wrist_face": wrist_face,
            "slip_dir": lead_dir * head_dx,
        },
        "shoulder_rotation": {
            "start": round(shoulder_rots[0], 4),
            "end": round(shoulder_rots[-1], 4),
            "range": round(max(shoulder_rots) - min(shoulder_rots), 4),
        },
        "block": {
            "above_fraction": round(above / count, 3),
            "near_fraction": round(near / count, 3),
            "above_elbows_fraction": round(above_elbows / count, 3),
            "above_nose_fraction": round(above_nose / count, 3),
            "peak_wrist_speed": block_speed,
            "wrist_path": left["path"] + right["path"],
        },
        "wrist_path_max": wrist_path_max,
        "sensor_drop_cm": sensor_drop,
    }
    return features, series


def extract_features(frames: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Public feature extractor (no internal speed series)."""
    features, _ = _extract(frames)
    return features


def window_visibility(frames: Sequence[dict[str, Any]],
                      stance: str = "orthodox") -> dict[str, Any]:
    """Frame-level validation: was the head and were both hands visible?

    Uses the newest image landmarks, because framing is a camera question and
    world landmarks are not screen-normalised. Visibility below the model's
    threshold counts as out of frame.
    """
    parsed = _parse_frames(frames)
    empty = {
        "head_visible": False,
        "hands_visible": {"lead": False, "rear": False},
        "in_frame": False,
    }
    if not parsed:
        return empty
    pts = parsed[-1].get("image")
    if pts is None or len(pts) < 33:
        return empty

    min_vis = THRESHOLDS["min_visibility"]

    def tracked(i: int) -> bool:
        p = pts[i]
        return (p[3] >= min_vis and 0.02 <= p[0] <= 0.98
                and 0.02 <= p[1] <= 0.98)

    head = tracked(0) or (tracked(L_EAR) and tracked(R_EAR))
    lead_side = STANCES.get(stance, STANCES["orthodox"])["lead"]
    lead = L_WRIST if lead_side == "left" else R_WRIST
    rear = R_WRIST if lead_side == "left" else L_WRIST
    hands = {"lead": tracked(lead), "rear": tracked(rear)}
    return {
        "head_visible": bool(head),
        "hands_visible": hands,
        "in_frame": bool(head and hands["lead"] and hands["rear"]),
    }


# ----------------------------------------------------------------------
# classification
# ----------------------------------------------------------------------
def _frac(conditions: Sequence[bool]) -> float:
    return sum(1 for c in conditions if c) / max(1, len(conditions))


def _straight_candidates(features: dict[str, Any], stance: str) -> list[dict[str, Any]]:
    out = []
    lead = STANCES.get(stance, STANCES["orthodox"])["lead"]
    for side in ("left", "right"):
        h = features.get(side) or {}
        other = features.get("right" if side == "left" else "left") or {}
        depth = float(h.get("depth_drop") or 0.0)
        elbow = h.get("elbow_max")
        travel = float(h.get("travel") or 0.0)
        horizontal = max(abs(float(h.get("dx") or 0.0)), abs(float(h.get("dy") or 0.0)))
        sensor = features.get("sensor_drop_cm")
        if sensor is not None:
            # The real ultrasonic Z is authoritative when available; do not
            # let the monocular world-z prediction override the sensor.
            straight_primary = float(sensor) >= THRESHOLDS["sensor_straight_drop_cm"]
        else:
            straight_primary = depth >= THRESHOLDS["straight_depth_drop"]
        conds = [
            straight_primary,
            elbow is not None and elbow >= THRESHOLDS["straight_elbow_extended"],
            travel >= THRESHOLDS["straight_travel"],
            depth >= float(other.get("depth_drop") or 0.0) + THRESHOLDS["straight_depth_lead"],
            depth >= THRESHOLDS["straight_forward_ratio"] * max(horizontal, 1e-6),
        ]
        out.append({
            "action": "jab" if side == lead else "cross",
            "hand": side,
            "pattern": "straight",
            "confidence": _frac(conds),
            "conditions": conds,
            "primary": bool(conds[0]),
        })
    return out


def _hook_candidates(features: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for side in ("left", "right"):
        h = features.get(side) or {}
        other = features.get("right" if side == "left" else "left") or {}
        across = float(h.get("across") or 0.0)
        elbow = h.get("elbow_at_max_travel")
        dy = abs(float(h.get("dy") or 0.0))
        travel = float(h.get("travel") or 0.0)
        conds = [
            across >= THRESHOLDS["hook_cross_travel"],
            elbow is not None and THRESHOLDS["hook_elbow_min"] <= elbow <= THRESHOLDS["hook_elbow_max"],
            dy <= THRESHOLDS["hook_vertical_max"],
            travel >= THRESHOLDS["hook_travel"],
            across >= float(other.get("across") or 0.0) + THRESHOLDS["hook_cross_lead"],
        ]
        out.append({
            "action": "hook_l" if side == "left" else "hook_r",
            "hand": side,
            "pattern": "hook",
            "confidence": _frac(conds),
            "conditions": conds,
            "primary": bool(conds[0]),
        })
    return out


def _uppercut_candidates(features: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for side in ("left", "right"):
        h = features.get(side) or {}
        other = features.get("right" if side == "left" else "left") or {}
        elbow = h.get("elbow_at_min_y")
        rise = float(h.get("rise") or 0.0)
        start_below = float(h.get("start_below") or 0.0)
        travel = float(h.get("travel") or 0.0)
        conds = [
            start_below >= THRESHOLDS["uppercut_start_below"],
            rise >= THRESHOLDS["uppercut_rise"],
            elbow is not None and elbow <= THRESHOLDS["uppercut_elbow_max"],
            travel >= THRESHOLDS["uppercut_travel"],
            rise >= float(other.get("rise") or 0.0) + THRESHOLDS["uppercut_rise_lead"],
        ]
        out.append({
            "action": "uppercut_l" if side == "left" else "uppercut_r",
            "hand": side,
            "pattern": "uppercut",
            "confidence": _frac(conds),
            "conditions": conds,
            "primary": bool(conds[0] and conds[1]),
        })
    return out


def _slip_candidates(features: dict[str, Any]) -> list[dict[str, Any]]:
    head = features.get("head") or {}
    dx = float(head.get("dx") or 0.0)
    dy = float(head.get("dy") or 0.0)
    slip_dir = float(head.get("slip_dir") or 0.0)
    conds = [
        abs(dx) >= THRESHOLDS["slip_head_dx"],
        abs(dy) <= THRESHOLDS["slip_head_dy_ratio"] * max(abs(dx), 1e-6),
        float(head.get("wrist_face") or 99.0) <= THRESHOLDS["slip_wrist_face"],
        float(head.get("path") or 0.0) >= THRESHOLDS["slip_head_path_min"] and
        float(head.get("path") or 0.0) >=
        THRESHOLDS["slip_head_share"] * max(float(features.get("wrist_path_max") or 0.0), 1e-6),
    ]
    if abs(dx) < 1e-9:
        return []
    return [{
        "action": "slip_l" if slip_dir > 0 else "slip_r",
        "hand": "left" if slip_dir > 0 else "right",
        "pattern": "slip",
        "confidence": _frac(conds),
        "conditions": conds,
        "primary": bool(conds[0]),
    }]


def _block_candidates(features: dict[str, Any]) -> list[dict[str, Any]]:
    block = features.get("block") or {}
    head = features.get("head") or {}
    conds = [
        float(block.get("above_fraction") or 0.0) >= THRESHOLDS["block_high_fraction"],
        float(block.get("near_fraction") or 0.0) >= THRESHOLDS["block_high_fraction"],
        float(block.get("above_elbows_fraction") or 0.0) >= THRESHOLDS["block_high_fraction"],
        float(block.get("above_nose_fraction") or 0.0) >= THRESHOLDS["block_high_fraction"],
        abs(float(head.get("dx") or 0.0)) <= THRESHOLDS["block_head_dx"]
        and abs(float(head.get("dy") or 0.0)) <= THRESHOLDS["block_head_dy"],
        float(block.get("peak_wrist_speed") or 0.0) <= THRESHOLDS["block_max_wrist_speed"],
        float(block.get("wrist_path") or 0.0) <= THRESHOLDS["block_max_wrist_path"],
    ]
    return [{
        "action": "block",
        "hand": "both",
        "pattern": "block",
        "confidence": _frac(conds),
        "conditions": conds,
        "primary": bool(conds[0] and conds[1]),
    }]


def _execution_ms(action: str | None, side: str | None,
                  series: Sequence[dict[str, float]]) -> int | None:
    if not action or len(series) < 3:
        return None
    if action.startswith("slip"):
        values = [s["head_speed"] for s in series]
    elif action == "block":
        values = [(s["left_speed"] + s["right_speed"]) / 2.0 for s in series]
    else:
        key = "left_speed" if side == "left" else "right_speed"
        values = [s[key] for s in series]
    peak = max(values) if values else 0.0
    if peak <= THRESHOLDS["exec_floor"]:
        return None
    cut = max(THRESHOLDS["exec_floor"], THRESHOLDS["exec_speed_ratio"] * peak)
    peak_i = max(range(len(values)), key=lambda i: values[i])
    first_i = peak_i
    while first_i > 0 and values[first_i - 1] >= cut:
        first_i -= 1
    last_i = peak_i
    while last_i + 1 < len(values) and values[last_i + 1] >= cut:
        last_i += 1
    return int(round((series[last_i]["t"] - series[first_i]["t"]) * 1000.0))


_PRIORITY = {
    "block": 0, "slip_l": 1, "slip_r": 2,
    "uppercut_l": 3, "uppercut_r": 4,
    "hook_l": 5, "hook_r": 6, "jab": 7, "cross": 8,
}


def classify_window(frames: Sequence[dict[str, Any]], cue: str | None = None,
                    stance: str = "orthodox") -> dict[str, Any]:
    """Classify one response window.

    Returns ``{"action", "hand", "confidence", "execution_ms", "features"}``.
    ``action`` is ``None`` when no candidate reaches ``CONFIDENCE_MIN``.
    ``cue`` is only used to break exact confidence ties; it never invents an
    action the geometry did not support.
    """
    features, series = _extract(frames)
    # The ultrasonic gate is authoritative for straights. It must not boost a
    # straight candidate on a hook/uppercut/slip cue just because a hand passed
    # the gate; those actions are classified from pose geometry.
    if cue not in ("jab", "cross"):
        features["sensor_drop_cm"] = None
    if features.get("n", 0) < THRESHOLDS["min_frames"]:
        return {"action": None, "hand": None, "confidence": 0.0,
                "execution_ms": None, "features": features,
                "candidates": {}}

    candidates: list[dict[str, Any]] = []
    candidates += _straight_candidates(features, stance)
    candidates += _hook_candidates(features)
    candidates += _uppercut_candidates(features)
    candidates += _slip_candidates(features)
    candidates += _block_candidates(features)

    by_action = {c["action"]: {
        "confidence": round(c["confidence"], 3),
        "conditions": [bool(v) for v in c["conditions"]],
        "primary": bool(c.get("primary")),
    } for c in candidates}

    # A fraction of conditions is not enough on its own: a slip whose head
    # never crossed the threshold must not win because two secondary
    # conditions happened to be true.  Discard candidates whose defining
    # condition was not met, then use the fraction among the survivors.
    valid = [c for c in candidates if c.get("primary")]
    best = max(valid or candidates, key=lambda c: (
        c["confidence"],
        1 if cue and c["action"] == cue else 0,
        -_PRIORITY.get(c["action"], 99),
    ))
    if not valid or best["confidence"] < CONFIDENCE_MIN:
        return {"action": None, "hand": None,
                "confidence": round(float(best["confidence"]), 3),
                "execution_ms": None, "features": features,
                "candidates": by_action}

    action = best["action"]
    side = best["hand"] if best["hand"] in ("left", "right") else None
    return {
        "action": action,
        "hand": best["hand"],
        "confidence": round(float(best["confidence"]), 3),
        "execution_ms": _execution_ms(action, side, series),
        "features": features,
        "candidates": by_action,
    }


# ----------------------------------------------------------------------
# onset / reaction time
# ----------------------------------------------------------------------
def _expected_part(cue: str, stance: str) -> str:
    if cue.startswith("slip"):
        return "head"
    if cue == "block":
        return "both"
    return resolve_hand(cue, stance)


def onset_ms(frames: Sequence[dict[str, Any]], cue: str, stance: str = "orthodox",
             t0: float = 0.0) -> int | None:
    """First post-t0 frame where the expected body part clearly moves.

    Baseline is taken from the frames before t0.  Returns integer milliseconds
    or ``None`` when the window never rises above the noise floor.
    """
    _, series = _extract(frames)
    if len(series) < 3:
        return None
    part = _expected_part(cue, stance)
    if part == "head":
        raw = [s["head_speed"] for s in series]
    elif part == "both":
        raw = [(s["left_speed"] + s["right_speed"]) / 2.0 for s in series]
    elif part == "left":
        raw = [s["left_speed"] for s in series]
    else:
        raw = [s["right_speed"] for s in series]

    # A short centred moving average suppresses single-frame pose jitter.
    # The timestamp we return is still the first frame of the run, so this
    # filters the threshold, not the measured time.
    values = []
    for i, v in enumerate(raw):
        lo = max(0, i - 1)
        hi = min(len(raw), i + 2)
        values.append(sum(raw[lo:hi]) / (hi - lo))

    base = [v for s, v in zip(series, values)
            if t0 - THRESHOLDS["onset_baseline_s"] <= s["t"] < t0]
    if len(base) < 3:
        base = values[:min(3, len(values))]
    mu = sum(base) / max(1, len(base))
    var = sum((v - mu) ** 2 for v in base) / max(1, len(base) - 1)
    std = math.sqrt(max(0.0, var))
    thr = max(mu + THRESHOLDS["onset_k"] * std,
              THRESHOLDS["onset_floor"], mu * 2.2)

    run = 0
    first_t: float | None = None
    need = int(THRESHOLDS["onset_need"])
    for s, v in zip(series, values):
        if s["t"] < t0:
            continue
        if v > thr:
            if run == 0:
                first_t = s["t"]
            run += 1
            if run >= need:
                return int(round((first_t - t0) * 1000.0))
        else:
            run = 0
            first_t = None
    return None


def score_cue(cue: str, detection: dict[str, Any],
              reaction_ms: int | None) -> tuple[str, int | None]:
    """Return (outcome, execution_ms).

    outcome is ``"correct"``, ``"wrong"`` or ``"miss"``.  A detection below
    ``CONFIDENCE_MIN`` or a missing onset is a miss; a confident different
    action is a wrong action.
    """
    execution = detection.get("execution_ms") if detection else None
    action = (detection or {}).get("action")
    confidence = float((detection or {}).get("confidence") or 0.0)
    if not action or confidence < CONFIDENCE_MIN:
        return "miss", execution
    if action == cue == "block":
        # A block is a held posture, not a single movement.  At 10-12 fps the
        # small guard raise may not produce a measurable onset, but the pose
        # is still a valid block.  Keep reaction_ms None and score the action.
        return "correct", execution
    if reaction_ms is None:
        return "miss", execution
    if action == cue:
        return "correct", execution
    return "wrong", execution


def grade_for(reaction_ms: int | None, correct: bool) -> str:
    if not correct:
        return "R"
    if reaction_ms is None:
        # Correct action, but the camera could not measure an onset (usually
        # a held block at a sparse frame rate).  Neutral yellow, no speed XP.
        return "Y"
    if reaction_ms <= GREEN_MS:
        return "G"
    if reaction_ms <= YELLOW_MS:
        return "Y"
    return "R"


# ----------------------------------------------------------------------
# synthetic landmarks (tests + VISION=fake)
# ----------------------------------------------------------------------
_GUARD = {
    "left": {"wrist": (0.560, 0.310, -0.030), "elbow": (0.650, 0.470, 0.030)},
    "right": {"wrist": (0.440, 0.310, -0.030), "elbow": (0.350, 0.470, 0.030)},
}
_DOWN = {
    "left": {"wrist": (0.620, 0.620, 0.000), "elbow": (0.640, 0.520, 0.000)},
    "right": {"wrist": (0.380, 0.620, 0.000), "elbow": (0.360, 0.520, 0.000)},
}

# Peak wrist/elbow positions for one side.  Mirrored for the other side by
# the generator below.  These are stylised keyframes, not biomechanics.
_PEAKS = {
    "straight": {
        "hand": None,
        "wrist": (0.560, 0.310, -0.300),
        "elbow": (0.588, 0.360, -0.135),
    },
    "hook": {
        "hand": None,
        "wrist": (0.430, 0.315, -0.090),
        "elbow": (0.630, 0.330, 0.000),
    },
    "uppercut": {
        "hand": None,
        "wrist": (0.520, 0.300, -0.120),
        "elbow": (0.640, 0.380, 0.000),
    },
}


def _mirror(p: tuple[float, float, float]) -> tuple[float, float, float]:
    return (1.0 - p[0], p[1], p[2])


def _mix(a: Sequence[float], b: Sequence[float], u: float) -> tuple[float, float, float]:
    return (a[0] + (b[0] - a[0]) * u,
            a[1] + (b[1] - a[1]) * u,
            a[2] + (b[2] - a[2]) * u)


def _smooth(u: float) -> float:
    u = max(0.0, min(1.0, u))
    return u * u * (3.0 - 2.0 * u)


def _phase(s: float, onset: float, move: float, hold: float,
           return_s: float) -> tuple[float, float]:
    """Return (extension, return) progress for a punch-like action."""
    if s <= onset:
        return 0.0, 0.0
    if s <= onset + move:
        return _smooth((s - onset) / move), 0.0
    if s <= onset + move + hold:
        return 1.0, 0.0
    back = (s - (onset + move + hold)) / max(1e-6, return_s)
    return 1.0, _smooth(back)


def _pose_at(action: str, s: float, stance: str) -> tuple[dict[str, tuple[float, float, float]],
                                                          dict[str, tuple[float, float, float]]]:
    """Return (wrists, elbows) for the requested action at local time s."""
    wrists = {k: v["wrist"] for k, v in _GUARD.items()}
    elbows = {k: v["elbow"] for k, v in _GUARD.items()}
    if action in ("none", "unknown"):
        wrists = {k: v["wrist"] for k, v in _DOWN.items()}
        elbows = {k: v["elbow"] for k, v in _DOWN.items()}
        return wrists, elbows

    if action.startswith("slip"):
        onset, move, hold, total = 0.30, 0.20, 0.18, 0.32
        ext, back = _phase(s, onset, move, hold, total)
        u = ext * (1.0 - back)
        direction = 1.0 if action == "slip_l" else -1.0
        shift = direction * 0.120 * u
        for side in ("left", "right"):
            w = _GUARD[side]["wrist"]
            e = _GUARD[side]["elbow"]
            # Head moves more than the hands; the hands follow about half.
            wrists[side] = (w[0] + shift * 0.55, w[1], w[2])
            elbows[side] = (e[0] + shift * 0.35, e[1], e[2])
        return wrists, elbows

    if action == "block":
        onset, move, hold, total = 0.28, 0.25, 0.30, 0.32
        ext, back = _phase(s, onset, move, hold, total)
        u = ext * (1.0 - back)
        peak = {"left": (0.535, 0.290, -0.045), "right": (0.465, 0.290, -0.045)}
        for side in ("left", "right"):
            wrists[side] = _mix(_GUARD[side]["wrist"], peak[side], u)
        return wrists, elbows

    if action in ("jab", "cross"):
        side = resolve_hand(action, stance)
        peak = _PEAKS["straight"]
        onset, move, hold, total = 0.30, 0.20, 0.22, 0.30
    elif action in ("hook_l", "hook_r"):
        side = "left" if action == "hook_l" else "right"
        peak = _PEAKS["hook"]
        onset, move, hold, total = 0.30, 0.20, 0.20, 0.32
    elif action in ("uppercut_l", "uppercut_r"):
        side = "left" if action == "uppercut_l" else "right"
        peak = _PEAKS["uppercut"]
        onset, move, hold, total = 0.30, 0.22, 0.20, 0.30
    else:
        side = "left"
        peak = _PEAKS["straight"]
        onset, move, hold, total = 0.30, 0.20, 0.22, 0.30

    ext, back = _phase(s, onset, move, hold, total)
    u = ext * (1.0 - back)

    def target(p: tuple[float, float, float]) -> tuple[float, float, float]:
        return p if side == "left" else _mirror(p)

    if action in ("uppercut_l", "uppercut_r"):
        start_w = _DOWN[side]["wrist"]
        start_e = _DOWN[side]["elbow"]
    else:
        start_w = _GUARD[side]["wrist"]
        start_e = _GUARD[side]["elbow"]
    wrists[side] = _mix(start_w, target(peak["wrist"]), u)
    elbows[side] = _mix(start_e, target(peak["elbow"]), u)
    return wrists, elbows


def _landmarks(wrists: dict[str, tuple[float, float, float]],
               elbows: dict[str, tuple[float, float, float]],
               head_shift: float = 0.0) -> tuple[list[tuple[float, float, float, float]],
                                                  list[tuple[float, float, float]]]:
    pts: list[tuple[float, float, float, float]] = [(0.0, 0.0, 0.0, 0.0) for _ in range(33)]
    pts[NOSE] = (0.500 + head_shift, 0.300, 0.000, 1.0)
    pts[2] = (0.515 + head_shift, 0.285, -0.005, 1.0)
    pts[5] = (0.485 + head_shift, 0.285, -0.005, 1.0)
    pts[L_EAR] = (0.535 + head_shift, 0.300, 0.000, 1.0)
    pts[R_EAR] = (0.465 + head_shift, 0.300, 0.000, 1.0)
    pts[L_SHOULDER] = (0.600, 0.400, 0.000, 1.0)
    pts[R_SHOULDER] = (0.400, 0.400, 0.000, 1.0)
    pts[L_ELBOW] = (*elbows["left"], 1.0)
    pts[R_ELBOW] = (*elbows["right"], 1.0)
    pts[L_WRIST] = (*wrists["left"], 1.0)
    pts[R_WRIST] = (*wrists["right"], 1.0)
    pts[L_HIP] = (0.550, 0.650, 0.000, 1.0)
    pts[R_HIP] = (0.450, 0.650, 0.000, 1.0)
    # World landmarks are the same geometry in metres.  Shoulder width is
    # 0.2 in the stylised image, so a 2x scale gives a 0.4 m shoulder.
    world = [(p[0] * 2.0, p[1] * 2.0, p[2] * 2.0) for p in pts]
    return pts, world


def synthetic_window(action: str, stance: str = "orthodox", t0: float = 0.0,
                     fps: int = 30, duration: float = 1.2,
                     pre_roll: float = 0.35) -> list[dict[str, Any]]:
    """Deterministic synthetic landmark window for tests and VISION=fake.

    ``t0`` is the stimulus time.  Frames run from ``t0 - pre_roll`` to
    ``t0 + duration`` at ``fps``.  The action starts 300 ms after t0, so the
    expected reaction time is ~300 ms.
    """
    if action not in ACTION_CATALOG and action not in ("none", "unknown"):
        raise ValueError(f"unknown synthetic action: {action}")
    frames: list[dict[str, Any]] = []
    n = int(round((pre_roll + duration) * fps))
    for i in range(n + 1):
        t = t0 - pre_roll + i / fps
        s = t - t0
        wrists, elbows = _pose_at(action, s, stance)
        head_shift = 0.0
        if action.startswith("slip"):
            # Mirror the head displacement used for the hands.
            onset, move, hold, total = 0.30, 0.20, 0.18, 0.32
            ext, back = _phase(s, onset, move, hold, total)
            u = ext * (1.0 - back)
            head_shift = (1.0 if action == "slip_l" else -1.0) * 0.120 * u
        image, world = _landmarks(wrists, elbows, head_shift)
        frames.append({"t": t, "image": image, "world": world})
    return frames


# A clear alias for tests written against the spec wording.
fake_window = synthetic_window


# ----------------------------------------------------------------------
# catalog for REST / docs
# ----------------------------------------------------------------------
def cue_payload(action: str, stance: str = "orthodox") -> dict[str, Any]:
    item = ACTION_CATALOG[action]
    return {
        "id": item["id"],
        "action": item["id"],
        "label": item["label"],
        "hand": item["hand"],
        "hand_side": resolve_hand(action, stance),
        "pattern": item["pattern"],
        "family": item["family"],
        "cue": item["cue"],
        "stance": stance,
    }


def catalog(stance: str = "orthodox") -> dict[str, Any]:
    stance = stance if stance in STANCES else "orthodox"
    return {
        "mode": "boxing",
        "stance": stance,
        "stances": STANCES,
        "lead_hand": STANCES[stance]["lead"],
        "rear_hand": STANCES[stance]["rear"],
        "actions": [cue_payload(a, stance) for a in ACTION_CATALOG],
        "default_set": list(DEFAULT_SET),
        "window_ms": WINDOW_MS,
        "delay_min_ms": DELAY_MIN_MS,
        "delay_max_ms": DELAY_MAX_MS,
        "confidence_min": CONFIDENCE_MIN,
        "grade_ms": {"green": GREEN_MS, "yellow": YELLOW_MS},
        "thresholds": dict(THRESHOLDS),
    }
