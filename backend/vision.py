"""Camera + pose pipeline.

Three interchangeable sources, one sample stream:

  local   laptop webcam via OpenCV, processed in a background thread
  remote  JPEG frames posted by a phone over the websocket
  fake    synthetic speed samples, so the whole drill loop runs with no camera

Timestamps are always taken at capture time.  The primary pose path is the
MediaPipe Tasks **Pose Landmarker** (`models/pose_landmarker_lite.task`,
VIDEO mode).  A legacy `mp.solutions.pose` adapter is kept only as a fallback
when Tasks creation fails, and frame-difference motion energy is the final
fallback.  The racing path works without pose; the boxing mode needs the
landmark arrays.

MediaPipe is optional at import time.  Unit tests use the synthetic landmark
generator and never require the model.
"""
from __future__ import annotations

import base64
import math
import queue
import random
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any

from . import config

try:
    import cv2
    import numpy as np

    CV2_OK = True
except Exception:  # pragma: no cover - import guard
    cv2 = None
    np = None
    CV2_OK = False

try:
    import mediapipe as mp

    MP_OK = True
except Exception:  # pragma: no cover - import guard
    mp = None
    MP_OK = False

try:
    from mediapipe.tasks import python as mp_python
    from mediapipe.tasks.python import vision as mp_vision

    MP_TASKS_OK = True
except Exception:  # pragma: no cover - import guard
    mp_python = None
    mp_vision = None
    MP_TASKS_OK = False

try:  # pragma: no cover - the legacy adapter is exercised only at runtime
    from mediapipe.python.solutions import pose as mp_legacy_pose

    MP_LEGACY_OK = True
except Exception:  # pragma: no cover - import guard
    mp_legacy_pose = None
    MP_LEGACY_OK = False


SELECTED = (0, 11, 12, 15, 16, 23, 24)  # nose, shoulders, wrists, hips


@dataclass
class Sample:
    t: float          # timestamp in the source's own clock
    t_recv: float     # backend perf_counter when this sample was produced
    speed: float
    source: str       # local | remote | fake
    clock: str
    floor: float
    # Pose payload (all 33 landmarks).  The racing path ignores these; the
    # boxing classifier needs them.  None when the motion fallback is active.
    landmarks: list | None = None   # image: (x, y, z, visibility)
    world: list | None = None       # world: (x, y, z) in metres


class VisionEngine:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.samples: deque[Sample] = deque(maxlen=1200)

        self.mode = config.VISION_MODE
        self.local_ok = False
        self.local_error: str | None = None
        self.pose_ok = MP_OK and CV2_OK
        self.pose_error: str | None = None
        if self.mode == "fake":
            self.pose_backend: str | None = "fake"
        else:
            self.pose_backend = "pending" if self.pose_ok else "motion"
        self.pose_model = str(config.POSE_MODEL_PATH)

        self.remote_clock: str | None = None
        self.remote_seen_at = 0.0
        self.local_seen_at = 0.0
        self.last_speed = 0.0
        self.last_floor = config.ONSET_FLOOR_FAKE
        self.last_source = "none"
        self.frames = 0
        self.drops = 0
        self._proc_ms = 0.0

        self._q: queue.Queue = queue.Queue(maxsize=1)
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._latest_jpeg: bytes | None = None
        self._last_preview_t = 0.0
        self._prev_gray: dict[str, Any] = {}
        self._pose = None          # legacy fallback
        self._landmarker = None    # Tasks PoseLandmarker
        self._pose_ready = False
        self._video_ts_ms = 0
        self._fake_onset_at: float | None = None
        self._fake_started = False
        self._floors: dict[str, float] = {}

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    def start(self) -> None:
        if self.mode == "auto":
            self.mode = "local" if CV2_OK else "fake"

        if self.mode in ("local", "auto"):
            t = threading.Thread(target=self._capture_loop, name="vision-capture", daemon=True)
            t.start()
            self._threads.append(t)

        proc = threading.Thread(target=self._process_loop, name="vision-process", daemon=True)
        proc.start()
        self._threads.append(proc)

        if self.mode == "fake":
            self._start_fake()

    def _start_fake(self) -> None:
        if self._fake_started or self._stop.is_set():
            return
        self._fake_started = True
        if self.pose_backend in (None, "pending"):
            self.pose_backend = "fake"
        fake = threading.Thread(target=self._fake_loop, name="vision-fake", daemon=True)
        fake.start()
        self._threads.append(fake)

    def stop(self) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout=1.0)
        self._threads.clear()
        if self._landmarker is not None:
            try:
                self._landmarker.close()
            except Exception:
                pass
            self._landmarker = None
        if self._pose is not None:
            try:
                self._pose.close()
            except Exception:
                pass
            self._pose = None

    # ------------------------------------------------------------------
    # inputs
    # ------------------------------------------------------------------
    def submit_remote(self, jpeg_b64: str, t_client: float, clock: str) -> bool:
        if not CV2_OK:
            return False
        if len(jpeg_b64) > config.FRAME_MAX_CHARS:
            return False
        with self.lock:
            self.remote_clock = clock
            self.remote_seen_at = time.perf_counter()
        self._put_latest(("remote", jpeg_b64, float(t_client), clock))
        return True

    def simulate_onset(self, delay_s: float = 0.24) -> None:
        self._fake_onset_at = time.perf_counter() + max(0.02, delay_s)

    def simulate_boxing(self, cue_id: str, t0: float,
                        stance: str = "orthodox") -> int:
        """Append synthetic landmark frames for one boxing cue.

        The frames cover 350 ms of pre-stimulus rest plus 1.2 s of action at
        30 fps, so a fake boxing drill has the same window shape as a real
        one.  Timestamps are in the backend clock, which is what the fake
        source uses.  Returns the number of frames appended.
        """
        from . import boxing  # local import keeps module import order simple

        frames = boxing.synthetic_window(cue_id, stance=stance, t0=float(t0))
        now = time.perf_counter()
        with self.lock:
            for frame in frames:
                self.samples.append(Sample(
                    t=float(frame["t"]),
                    t_recv=now,
                    speed=0.02,  # deliberately below any racing onset floor
                    source="fake",
                    clock="backend",
                    floor=config.ONSET_FLOOR_FAKE,
                    landmarks=frame.get("image"),
                    world=frame.get("world"),
                ))
            self._floors["fake"] = config.ONSET_FLOOR_FAKE
        return len(frames)

    def local_available(self) -> bool:
        if not CV2_OK:
            return False
        if self.mode in ("local", "auto"):
            return self.local_ok
        return False

    def remote_active(self) -> bool:
        return (time.perf_counter() - self.remote_seen_at) < 1.0

    def has_source(self, source: str) -> bool:
        if source == "remote":
            return self.remote_active()
        if source == "local":
            return self.local_available() and (time.perf_counter() - self.local_seen_at) < 1.5
        return True

    # ------------------------------------------------------------------
    # capture / fake
    # ------------------------------------------------------------------
    def _capture_loop(self) -> None:
        if not CV2_OK:
            self.local_error = "opencv not installed"
            self.mode = "fake"
            self._start_fake()
            return
        backend = cv2.CAP_DSHOW if hasattr(cv2, "CAP_DSHOW") and __import__("sys").platform == "win32" else 0
        cap = cv2.VideoCapture(config.CAMERA_INDEX, backend)
        if not cap or not cap.isOpened():
            self.local_error = f"camera {config.CAMERA_INDEX} did not open"
            cap.release()
            with self.lock:
                self.mode = "fake"
            self._start_fake()
            return
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, config.CAMERA_WIDTH)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, config.CAMERA_HEIGHT)
        cap.set(cv2.CAP_PROP_FPS, config.CAMERA_FPS)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self.local_ok = True
        while not self._stop.is_set():
            ok, frame = cap.read()
            if not ok or frame is None:
                time.sleep(0.02)
                continue
            t = time.perf_counter()
            self.local_seen_at = t
            # In auto mode the phone is the primary sensor. Do not let the
            # laptop camera compete for the single processing slot while the
            # phone is streaming; the local path resumes after 1 s of silence.
            if config.VISION_MODE == "auto" and self.remote_active():
                continue
            self._put_latest(("local", frame, t, "backend"))
        cap.release()

    def _fake_loop(self) -> None:
        while not self._stop.is_set():
            now = time.perf_counter()
            speed = 0.012 + random.random() * 0.018
            if self._fake_onset_at is not None:
                if now >= self._fake_onset_at:
                    speed = 0.9 + random.random() * 0.35
                    if now > self._fake_onset_at + 0.55:
                        self._fake_onset_at = None
                else:
                    speed *= 0.6
            with self.lock:
                self.samples.append(Sample(now, now, speed, "fake", "backend",
                                           config.ONSET_FLOOR_FAKE))
                self.last_speed = speed
                self.last_floor = config.ONSET_FLOOR_FAKE
                self.last_source = "fake"
                self._floors["fake"] = config.ONSET_FLOOR_FAKE
            time.sleep(1.0 / 60.0)

    # ------------------------------------------------------------------
    # processing
    # ------------------------------------------------------------------
    def _put_latest(self, item: tuple) -> None:
        try:
            self._q.put_nowait(item)
        except queue.Full:
            try:
                self._q.get_nowait()
            except queue.Empty:
                pass
            self.drops += 1
            try:
                self._q.put_nowait(item)
            except queue.Full:
                pass

    def _process_loop(self) -> None:
        while not self._stop.is_set():
            try:
                source, payload, t_source, clock = self._q.get(timeout=0.2)
            except queue.Empty:
                continue
            started = time.perf_counter()
            frame = None
            if source == "remote":
                if not CV2_OK:
                    continue
                try:
                    raw = base64.b64decode(payload)
                    arr = np.frombuffer(raw, dtype=np.uint8)
                    frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                except Exception:
                    frame = None
            else:
                frame = payload

            if frame is None:
                continue

            speed, landmarks, world, floor = self._analyze(frame, source, t_source)
            now = time.perf_counter()
            with self.lock:
                self.samples.append(Sample(t_source, now, speed, source, clock, floor,
                                           landmarks=landmarks, world=world))
                self.last_speed = speed
                self.last_floor = floor
                self.last_source = source
                self._floors[source] = floor
                self.frames += 1
                self._proc_ms = 0.85 * self._proc_ms + 0.15 * ((now - started) * 1000.0)
            self._maybe_preview(frame, landmarks)

    def _ensure_pose(self) -> None:
        """Create the Tasks Pose Landmarker once, in the processing thread.

        Legacy `mp.solutions.pose` is only a fallback after Tasks creation
        fails.  Frame-difference motion is the final fallback.
        """
        if self._pose_ready:
            return
        self._pose_ready = True
        if not (CV2_OK and MP_OK) or not self.pose_ok:
            self.pose_backend = "motion"
            if self.pose_error is None:
                self.pose_error = ("opencv unavailable" if not CV2_OK
                                   else "mediapipe unavailable")
            return

        if MP_TASKS_OK and mp is not None and mp_python is not None and mp_vision is not None:
            try:
                options = mp_vision.PoseLandmarkerOptions(
                    base_options=mp_python.BaseOptions(
                        model_asset_path=str(config.POSE_MODEL_PATH)),
                    running_mode=mp_vision.RunningMode.VIDEO,
                    num_poses=1,
                    min_pose_detection_confidence=0.5,
                    min_tracking_confidence=0.5,
                )
                self._landmarker = mp_vision.PoseLandmarker.create_from_options(options)
                self.pose_backend = "tasks"
                self.pose_ok = True
                self.pose_error = None
                return
            except Exception as exc:  # pragma: no cover - model/runtime failure
                self.pose_error = f"tasks: {type(exc).__name__}: {exc}"

        if MP_LEGACY_OK and mp_legacy_pose is not None:
            try:
                self._pose = mp_legacy_pose.Pose(
                    static_image_mode=False,
                    model_complexity=0,
                    enable_segmentation=False,
                    min_detection_confidence=0.5,
                    min_tracking_confidence=0.5,
                )
                self.pose_backend = "legacy"
                self.pose_ok = True
                return
            except Exception as exc:  # pragma: no cover - defensive
                self.pose_error = (self.pose_error or "") + \
                    f"; legacy: {type(exc).__name__}: {exc}"

        self.pose_ok = False
        self.pose_backend = "motion"

    def _video_timestamp_ms(self, t_capture: float | None) -> int:
        """Strictly increasing millisecond timestamp for Tasks VIDEO mode."""
        ts = float(t_capture) if t_capture is not None else time.perf_counter()
        if ts <= 0:
            ts = time.perf_counter()
        # Browser performance.now() is milliseconds; backend perf_counter is
        # seconds.  The clamp keeps the Tasks tracker happy across sources.
        if ts > 1e6:
            ts /= 1000.0
        ms = int(ts * 1000.0)
        if ms <= self._video_ts_ms:
            ms = self._video_ts_ms + 1
        self._video_ts_ms = ms
        return ms

    def _analyze(self, frame: Any, source: str,
                 t_capture: float | None = None
                 ) -> tuple[float, list | None, list | None, float]:
        """Return (speed, image_landmarks, world_landmarks, floor) for one frame."""
        if not CV2_OK:
            return 0.0, None, None, config.ONSET_FLOOR_FAKE

        h, w = frame.shape[:2]
        small = cv2.resize(frame, (160, 120), interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        prev = self._prev_gray.get(source)
        motion = 0.0
        if prev is not None:
            diff = cv2.absdiff(prev, gray)
            motion = float(diff.mean())
        self._prev_gray[source] = gray

        if not self.pose_ok:
            return max(0.0, motion), None, None, config.ONSET_FLOOR_MOTION

        self._ensure_pose()
        if self._landmarker is None and self._pose is None:
            return max(0.0, motion), None, None, config.ONSET_FLOOR_MOTION

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        world_pts = None
        if self._landmarker is not None:
            try:
                mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
                result = self._landmarker.detect_for_video(
                    mp_image, self._video_timestamp_ms(t_capture))
                pose = result.pose_landmarks[0] if result.pose_landmarks else None
                world = (result.pose_world_landmarks[0]
                         if result.pose_world_landmarks else None)
                if not pose:
                    return max(0.0, motion), None, None, config.ONSET_FLOOR_MOTION
                pts = [(float(p.x), float(p.y), float(getattr(p, "z", 0.0)),
                        float(getattr(p, "visibility", 0.0))) for p in pose]
                if world:
                    world_pts = [(float(p.x), float(p.y), float(p.z))
                                 for p in world]
            except Exception as exc:  # pragma: no cover - runtime failure
                self.pose_ok = False
                self.pose_error = f"tasks: {type(exc).__name__}: {exc}"
                self.pose_backend = "motion"
                return max(0.0, motion), None, None, config.ONSET_FLOOR_MOTION
        else:
            try:
                result = self._pose.process(rgb)
            except Exception as exc:  # pragma: no cover
                self.pose_ok = False
                self.pose_error = f"legacy: {type(exc).__name__}: {exc}"
                self.pose_backend = "motion"
                return max(0.0, motion), None, None, config.ONSET_FLOOR_MOTION
            lms = result.pose_landmarks.landmark if result.pose_landmarks else None
            if not lms:
                return max(0.0, motion), None, None, config.ONSET_FLOOR_MOTION
            pts = [(float(p.x), float(p.y), float(getattr(p, "z", 0.0)),
                    float(getattr(p, "visibility", 0.0))) for p in lms]
            wl = getattr(result, "pose_world_landmarks", None)
            if wl and getattr(wl, "landmark", None):
                world_pts = [(float(p.x), float(p.y), float(p.z))
                             for p in wl.landmark]
        aspect = w / max(1, h)
        sw = math.hypot((pts[11][0] - pts[12][0]) * aspect, pts[11][1] - pts[12][1])
        if sw < 0.04:
            sw = 0.22
        now = time.perf_counter()
        prev_sample = getattr(self, "_last_pose", None)
        if getattr(self, "_last_pose_source", None) != source:
            prev_sample = None  # do not diff landmarks across cameras
        speed = 0.0
        if prev_sample and (now - prev_sample[0]) > 1e-4:
            dt = max(1e-3, min(0.2, now - prev_sample[0]))
            disp = 0.0
            for i in SELECTED:
                dx = (pts[i][0] - prev_sample[1][i][0]) * aspect
                dy = pts[i][1] - prev_sample[1][i][1]
                disp += math.hypot(dx, dy)
            speed = (disp / len(SELECTED)) / sw / dt
        self._last_pose = (now, pts)
        self._last_pose_source = source
        self._last_pts = pts
        return max(0.0, speed), pts, world_pts, config.ONSET_FLOOR_POSE

    def _maybe_preview(self, frame: Any, landmarks: list | None) -> None:
        now = time.perf_counter()
        if now - self._last_preview_t < 1.0 / max(1, config.PREVIEW_FPS):
            return
        self._last_preview_t = now
        if not CV2_OK:
            return
        vis = frame.copy()
        if landmarks:
            pts = landmarks

            def px(i: int) -> tuple[int, int]:
                h, w = vis.shape[:2]
                return int(pts[i][0] * w), int(pts[i][1] * h)

            for a, b in ((11, 12), (11, 23), (12, 24), (23, 24), (11, 15), (12, 16)):
                try:
                    cv2.line(vis, px(a), px(b), (80, 255, 150), 2, cv2.LINE_AA)
                except Exception:
                    pass
            for i in SELECTED:
                try:
                    cv2.circle(vis, px(i), 4, (240, 240, 240), -1, cv2.LINE_AA)
                except Exception:
                    pass
            try:
                cv2.putText(vis, f"{self.last_speed:4.2f}", (12, 28),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (80, 255, 150), 2, cv2.LINE_AA)
            except Exception:
                pass
        try:
            ok, buf = cv2.imencode(".jpg", vis, [int(cv2.IMWRITE_JPEG_QUALITY),
                                                 config.PREVIEW_QUALITY])
            if ok:
                with self.lock:
                    self._latest_jpeg = buf.tobytes()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # outputs
    # ------------------------------------------------------------------
    def samples_for(self, source: str, since: float = 0.0) -> list[Sample]:
        with self.lock:
            return [s for s in self.samples if s.source == source and s.t >= since]

    def latest_speed(self, source: str | None = None) -> float:
        with self.lock:
            for s in reversed(self.samples):
                if source is None or s.source == source:
                    return s.speed
        return 0.0

    def onset_floor(self, source: str | None = None) -> float:
        with self.lock:
            if source and source in self._floors:
                return self._floors[source]
            return self.last_floor

    def hold_still(self, source: str, window: float = config.HOLD_STILL_S) -> bool:
        now = time.perf_counter()
        samples = self.samples_for(source, since=now - window)
        if len(samples) < 3:
            return True
        speeds = [s.speed for s in samples]
        mean = sum(speeds) / len(speeds)
        peak = max(speeds)
        floor = self.onset_floor(source)
        if source == "remote":
            thr = max(config.STILL_MAX_POSE, floor * 2.5)
        elif source == "local":
            thr = max(config.STILL_MAX_POSE, floor * 2.5) if self.pose_ok else config.STILL_MAX_MOTION
        else:
            thr = config.STILL_MAX_POSE
        return mean < thr and peak < thr * 1.8

    def fps(self, source: str | None = None) -> float:
        now = time.perf_counter()
        with self.lock:
            recent = [
                s.t_recv for s in self.samples
                if now - s.t_recv <= 1.0 and (source is None or s.source == source)
            ]
        return float(len(recent))

    def latest_jpeg(self) -> bytes | None:
        with self.lock:
            return self._latest_jpeg

    def status(self) -> dict[str, Any]:
        with self.lock:
            active = ("remote" if self.remote_active()
                      else "local" if self.local_ok
                      else "fake")
            last = next((s for s in reversed(self.samples) if s.source == active), None)
            return {
                "mode": self.mode,
                "configured": config.VISION_MODE,
                "active_source": active,
                "local_ok": self.local_ok,
                "local_error": self.local_error,
                "pose_ok": self.pose_ok,
                "pose_error": self.pose_error,
                "pose_backend": self.pose_backend,
                "pose_model": self.pose_model,
                "pose_model_exists": config.POSE_MODEL_PATH.exists(),
                "remote_active": self.remote_active(),
                "remote_clock": self.remote_clock,
                "fps": round(self.fps(active), 1),
                "last_speed": round(last.speed if last else 0.0, 3),
                "last_source": last.source if last else "none",
                "frames": self.frames,
                "drops": self.drops,
                "proc_ms": round(self._proc_ms, 1),
                "has_preview": self._latest_jpeg is not None,
            }


def detect_onset(samples: list[tuple[float, float]], t0: float,
                 k: float = config.ONSET_K, floor: float = 0.1,
                 need: int = config.ONSET_NEED,
                 baseline_s: float = config.BASELINE_S) -> tuple[float, float] | None:
    """Return (onset_time, speed) or None.

    Baseline is the quiet window before the stimulus. Threshold is
    mean + k*std, floored so a dead-quiet camera cannot trigger on noise.
    """
    base = [s for t, s in samples if t0 - baseline_s <= t < t0]
    if len(base) < 4:
        return None
    mu = sum(base) / len(base)
    var = sum((s - mu) ** 2 for s in base) / max(1, len(base) - 1)
    std = math.sqrt(max(0.0, var))
    thr = max(mu + k * std, floor, mu * 2.2)
    run = 0
    first_t: float | None = None
    first_s = 0.0
    for t, s in sorted(samples, key=lambda x: x[0]):
        if t < t0:
            continue
        if s > thr:
            if run == 0:
                first_t = t
                first_s = s
            run += 1
            if run >= need:
                return first_t, first_s
        else:
            run = 0
            first_t = None
    return None
