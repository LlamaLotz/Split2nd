#!/usr/bin/env python3
"""Camera + pose bring-up for the laptop rig.

    python scripts/check_camera.py
    python scripts/check_camera.py --index 1 --seconds 5
    python scripts/check_camera.py --scan

The demo camera is the integrated Lenovo webcam: index 0, CAP_DSHOW on
Windows, MJPG 640x480 at 30 fps.  The script prints the measured resolution
and fps, warns on a black frame, and checks the pose framing that boxing
needs (head and both hands in frame).  It saves data/camera_test.jpg for a
visual check and adds no dependencies beyond the app requirements.
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

TARGET_WIDTH = 640
TARGET_HEIGHT = 480
TARGET_FPS = 30
BLACK_MEAN = 10.0
BLACK_STD = 5.0
TOP_EDGE_Y = 0.10          # nose closer than this to the top is a crop risk
HAND_EDGE = 0.03           # wrists this close to an edge are about to leave


def check_frame(frame) -> bool:
    """Print brightness/pose framing warnings.  True when the frame is usable."""
    ok = True
    mean, std = cv2.meanStdDev(frame)
    brightness = float(mean[0][0])
    spread = float(std[0][0])
    print(f"          brightness mean={brightness:.1f} std={spread:.1f}")
    if brightness < BLACK_MEAN or spread < BLACK_STD:
        ok = False
        print("          WARNING: the frame is black/near-black.")
        print("          Check the ThinkPad camera privacy shutter, Windows camera")
        print("          permissions, and that another app is not holding the camera.")

    try:
        import mediapipe as mp

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        with mp.solutions.pose.Pose(model_complexity=0,
                                    min_detection_confidence=0.5) as pose:
            result = pose.process(rgb)
        found = bool(result.pose_landmarks)
        print(f"          mediapipe pose: {'FOUND' if found else 'not found'}")
        if not found:
            ok = False
            print("          WARNING: no pose. Step back so the head and both hands")
            print("          are in frame, add light, and avoid a busy background.")
            return ok
        lm = result.pose_landmarks.landmark
        nose = lm[0]
        print(f"          nose y={nose.y:.3f} visibility={nose.visibility:.2f}")
        if nose.y < TOP_EDGE_Y:
            print(f"          WARNING: the nose is near the top edge (y={nose.y:.3f}).")
            print("          Step back or tilt the laptop lid up; the head is being cut.")
        for idx, name in ((15, "left wrist"), (16, "right wrist")):
            hand = lm[idx]
            edge = (hand.x < HAND_EDGE or hand.x > 1.0 - HAND_EDGE
                    or hand.y < HAND_EDGE or hand.y > 1.0 - HAND_EDGE)
            if hand.visibility < 0.5 or edge:
                print(f"          WARNING: {name} is not clearly in frame "
                      f"(x={hand.x:.2f} y={hand.y:.2f} vis={hand.visibility:.2f}).")
                print("          Boxing needs both hands visible; step back.")
                ok = False
        print(f"          shoulders y={lm[11].y:.3f}/{lm[12].y:.3f} "
              f"wrists x={lm[15].x:.3f}/{lm[16].x:.3f}")
    except Exception as exc:
        print(f"          mediapipe unavailable: {type(exc).__name__}: {exc}")
        print("          Framing was not checked (pose is optional for this script).")
    return ok


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", type=int, default=0,
                    help="camera index; the integrated Lenovo camera is 0")
    ap.add_argument("--seconds", type=float, default=4.0)
    ap.add_argument("--scan", action="store_true", help="try indices 0-3")
    args = ap.parse_args()

    backend_name = "DSHOW" if sys.platform == "win32" else "default"
    print("Camera target: index %d, %s, %dx%d @ %d fps, MJPG"
          % (args.index, backend_name, TARGET_WIDTH, TARGET_HEIGHT, TARGET_FPS))

    indices = range(4) if args.scan else [args.index]
    for index in indices:
        backend = cv2.CAP_DSHOW if sys.platform == "win32" else 0
        cap = cv2.VideoCapture(index, backend)
        if not cap.isOpened():
            print(f"[{index}] did not open")
            cap.release()
            continue
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, TARGET_WIDTH)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, TARGET_HEIGHT)
        cap.set(cv2.CAP_PROP_FPS, TARGET_FPS)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        print(f"[{index}] opened {w}x{h} (requested {TARGET_WIDTH}x{TARGET_HEIGHT})")
        if (w, h) != (TARGET_WIDTH, TARGET_HEIGHT):
            print(f"[{index}] WARNING: the camera negotiated a different size;"
                  " check the Windows camera driver and close other camera apps.")

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
        fps = frames / elapsed
        print(f"[{index}] measured {fps:.1f} fps over {elapsed:.1f}s")
        if fps < 8:
            print("[{0}] WARNING: below 8 fps. MediaPipe boxing needs >= 8 fps "
                  "(10-30 fps expected).".format(index))

        out = ROOT / "data" / "camera_test.jpg"
        out.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(out), last)
        print(f"[{index}] saved {out}")
        check_frame(last)

        cap.release()
        break
    else:
        print("No camera opened. Check the ThinkPad camera privacy switch and "
              "Windows camera permissions.")


if __name__ == "__main__":
    main()
