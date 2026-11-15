"""All constants and environment switches in one place.

Everything the demo can tune lives here so a coder never has to grep
through the state machine at T-20 minutes.
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = ROOT / "frontend"
DATA_DIR = Path(os.getenv("SPLIT2ND_DATA", str(ROOT / "data")))
CERT_DIR = Path(os.getenv("SPLIT2ND_CERTS", str(ROOT / "certs")))

VERSION = "0.1.0"

# ---------------------------------------------------------------------
# Hardware / vision switches
#   VISION=auto   -> try the laptop camera, fall back to fake samples
#   VISION=local  -> laptop camera only
#   VISION=remote -> only phone frames (laptop camera off)
#   VISION=fake   -> synthetic samples, no camera needed (dev + fallback)
#   RIG=auto      -> serial if an Arduino-looking port is present, else fake
# ---------------------------------------------------------------------
VISION_MODE = os.getenv("VISION", "auto").strip().lower()
RIG_MODE = os.getenv("RIG", "auto").strip().lower()
RIG_PORT = os.getenv("RIG_PORT", "").strip()

CAMERA_INDEX = int(os.getenv("CAMERA_INDEX", "0"))
CAMERA_WIDTH = int(os.getenv("CAMERA_WIDTH", "640"))
CAMERA_HEIGHT = int(os.getenv("CAMERA_HEIGHT", "480"))
CAMERA_FPS = int(os.getenv("CAMERA_FPS", "30"))

# Ultrasonic gate, centimetres from the sensor.
GATE_CM = int(os.getenv("GATE_CM", "30"))

# ---------------------------------------------------------------------
# Timing. All backend times are time.perf_counter() seconds.
# ---------------------------------------------------------------------
SIMPLE_GREEN_MS = int(os.getenv("GREEN_MS", "250"))
SIMPLE_YELLOW_MS = int(os.getenv("YELLOW_MS", "350"))
FALSE_START_MS = 100

REP_WINDOW_S = 1.5          # response window
BASELINE_S = 0.4            # pre-stimulus baseline
HOLD_STILL_S = 0.35         # required stillness before arming
ARM_TIMEOUT_S = 4.0         # give up if the athlete keeps fidgeting
PAINT_TIMEOUT_S = 1.5       # extra grace on top of the scheduled delay
RESULT_HOLD_S = 2.2         # result card dwell before idle / next rep

# F1-style start sequence. The server sends the full delay to lights-out.
LIGHTS_COUNT = 5
LIGHT_INTERVAL_MS = 420
DELAY_MIN_MS = 2500
DELAY_MAX_MS = 4800

ONSET_K = 4.0
ONSET_NEED = 2
ONSET_FLOOR_POSE = 0.06     # shoulder-widths / second
ONSET_FLOOR_MOTION = 1.20   # normalised frame-difference units
ONSET_FLOOR_FAKE = 0.08
STILL_MAX_POSE = 0.22
STILL_MAX_MOTION = 4.0

# ---------------------------------------------------------------------
# Game layer
# ---------------------------------------------------------------------
GAME_ENABLED = os.getenv("GAME", "1") == "1"
ROOM_TARGET_REPS = int(os.getenv("ROOM_REPS", "5"))
BOT_RT_RANGE = (205, 265)   # seeded opponent reaction times, ms

# ---------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8443"))
TLS_CERT = CERT_DIR / "server.crt"
TLS_KEY = CERT_DIR / "server.key"

# Preview stream
PREVIEW_FPS = 12
PREVIEW_QUALITY = 70
FRAME_MAX_CHARS = 600_000   # ~450 KB binary, plenty for 640x480 q65

# UI limits
LEADERBOARD_LIMIT = 12
HISTORY_LIMIT = 40
EVIDENCE_VERSION = "2026-11"
