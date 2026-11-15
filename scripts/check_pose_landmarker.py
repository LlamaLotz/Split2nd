#!/usr/bin/env python3
"""Verify the MediaPipe Tasks Pose Landmarker model loads."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODEL = ROOT / "models" / "pose_landmarker_lite.task"

try:
    import mediapipe as mp
    from mediapipe.tasks import python as mp_python
    from mediapipe.tasks.python import vision
except Exception as exc:
    print(f"mediapipe Tasks API unavailable: {type(exc).__name__}: {exc}")
    sys.exit(1)

if not MODEL.exists():
    print(f"missing model: {MODEL}")
    print("download: curl -L -o models/pose_landmarker_lite.task "
          "https://storage.googleapis.com/mediapipe-models/pose_landmarker/"
          "pose_landmarker_lite/float16/1/pose_landmarker_lite.task")
    sys.exit(1)

print(f"model: {MODEL} ({MODEL.stat().st_size / 1024 / 1024:.1f} MB)")
options = vision.PoseLandmarkerOptions(
    base_options=mp_python.BaseOptions(model_asset_path=str(MODEL)),
    running_mode=vision.RunningMode.IMAGE,
    num_poses=1,
    min_pose_detection_confidence=0.5,
)
landmarker = vision.PoseLandmarker.create_from_options(options)
print("POSE_LANDMARKER_OK")
landmarker.close()
