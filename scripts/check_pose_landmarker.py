#!/usr/bin/env python3
"""Check the MediaPipe Tasks Pose Landmarker adapter.

    python scripts/check_pose_landmarker.py
    python scripts/check_pose_landmarker.py --image frame.jpg
    python scripts/check_pose_landmarker.py --camera 0

Prints POSE_LANDMARKER_OK on success, POSE_LANDMARKER_FAIL otherwise.
This intentionally uses the Tasks API, not the legacy mp.solutions.pose.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend import config  # noqa: E402


def fail(message: str) -> int:
    print(f"POSE_LANDMARKER_FAIL: {message}")
    return 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=str(config.POSE_MODEL_PATH))
    ap.add_argument("--image", default="", help="optional still image")
    ap.add_argument("--camera", type=int, default=-1,
                    help="optional camera index; reads one frame")
    args = ap.parse_args()

    model = Path(args.model)
    if not model.exists():
        return fail(f"model not found: {model}")

    try:
        import cv2
        import mediapipe as mp
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision
    except Exception as exc:
        return fail(f"import: {type(exc).__name__}: {exc}")

    try:
        options = vision.PoseLandmarkerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=str(model)),
            running_mode=vision.RunningMode.VIDEO,
            num_poses=1,
            min_pose_detection_confidence=0.5,
            min_tracking_confidence=0.5,
        )
        landmarker = vision.PoseLandmarker.create_from_options(options)
    except Exception as exc:
        return fail(f"create: {type(exc).__name__}: {exc}")

    frame = None
    if args.image:
        frame = cv2.imread(args.image)
        if frame is None:
            landmarker.close()
            return fail(f"cannot read image: {args.image}")
    elif args.camera >= 0:
        cap = cv2.VideoCapture(args.camera)
        ok, frame = cap.read()
        cap.release()
        if not ok or frame is None:
            landmarker.close()
            return fail(f"camera {args.camera} did not return a frame")

    try:
        if frame is not None:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            result = landmarker.detect_for_video(image, int(time.perf_counter() * 1000))
            count = len(result.pose_landmarks[0]) if result.pose_landmarks else 0
            if count:
                print(f"pose_landmarks={count}")
            else:
                print("pose_landmarks=0 (no pose in the frame; model itself is OK)")
    finally:
        landmarker.close()

    print(f"POSE_LANDMARKER_OK model={model} backend=tasks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
