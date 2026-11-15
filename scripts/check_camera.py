#!/usr/bin/env python3
"""Camera + pose bring-up.

    python scripts/check_camera.py
    python scripts/check_camera.py --index 1 --seconds 5

Prints resolution, measured fps, and whether MediaPipe finds a pose.
Saves data/camera_test.jpg for a visual check.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    import cv2
except Exception as exc:
    print(f"OpenCV missing: {exc}")
    print("Install the app requirements: pip install -r requirements.txt")
    sys.exit(1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", type=int, default=0)
    ap.add_argument("--seconds", type=float, default=4.0)
    ap.add_argument("--scan", action="store_true", help="try indices 0-3")
    args = ap.parse_args()

    indices = range(4) if args.scan else [args.index]
    for index in indices:
        backend = cv2.CAP_DSHOW if sys.platform == "win32" else 0
        cap = cv2.VideoCapture(index, backend)
        if not cap.isOpened():
            print(f"[{index}] did not open")
            cap.release()
            continue
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        cap.set(cv2.CAP_PROP_FPS, 30)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        print(f"[{index}] opened {w}x{h}")

        ok, frame = cap.read()
        if not ok:
            print(f"[{index}] opened but returned no frame")
            cap.release()
            continue

        start = time.perf_counter()
        frames = 0
        last = frame
        while time.perf_counter() - start < args.seconds:
            ok, frame = cap.read()
            if ok:
                frames += 1
                last = frame
        elapsed = max(1e-6, time.perf_counter() - start)
        print(f"[{index}] {frames / elapsed:.1f} fps over {elapsed:.1f}s")

        out = ROOT / "data" / "camera_test.jpg"
        out.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(out), last)
        print(f"[{index}] saved {out}")

        try:
            import mediapipe as mp

            rgb = cv2.cvtColor(last, cv2.COLOR_BGR2RGB)
            with mp.solutions.pose.Pose(model_complexity=0,
                                        min_detection_confidence=0.5) as pose:
                result = pose.process(rgb)
            found = bool(result.pose_landmarks)
            print(f"[{index}] mediapipe pose: {'FOUND' if found else 'not found'}")
            if found:
                lm = result.pose_landmarks.landmark
                print(f"          shoulders y={lm[11].y:.3f}/{lm[12].y:.3f} "
                      f"wrists x={lm[15].x:.3f}/{lm[16].x:.3f}")
        except Exception as exc:
            print(f"[{index}] mediapipe unavailable: {type(exc).__name__}: {exc}")

        cap.release()
        break
    else:
        print("No camera opened. Check the ThinkPad camera privacy switch and Windows camera permissions.")


if __name__ == "__main__":
    main()
