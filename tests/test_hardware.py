"""Hardware-link tests: LED ownership, edge contract, camera health.

Everything runs without a real Arduino or camera.  The serial release path
uses a mock writer; the rig/engine paths use FakeRig.  The real COM port on
the demo laptop is never opened.
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend import boxing, config
from backend.drills import DrillEngine, edge_payload, feedback_color
from backend.net import ConnectionManager
from backend.rig import FakeRig, SerialRig, led_color
from backend.store import Store
from backend.vision import VisionEngine


# ----------------------------------------------------------------------
# feedback colour contract
# ----------------------------------------------------------------------
def test_racing_feedback_matches_the_grade():
    assert feedback_color("racing", "G") == "G"
    assert feedback_color("racing", "Y") == "Y"
    assert feedback_color("racing", "R") == "R"
    assert feedback_color("racing", "F", false_start=True) == "F"
    # A racing timeout is grade M; the edge is red, never a liveness signal.
    assert feedback_color("racing", "M", miss=True) == "R"


def test_boxing_feedback_is_action_first_not_rt_grade():
    # Correct cue, slow RT grade: green feedback.
    assert feedback_color("boxing", "R", correct=True) == "G"
    assert feedback_color("boxing", "Y", correct=True) == "G"
    assert feedback_color("boxing", "G", correct=True) == "G"
    # Wrong or miss: red regardless of any RT.
    assert feedback_color("boxing", "G", correct=False) == "R"
    assert feedback_color("boxing", "R", correct=False, miss=True) == "R"
    assert feedback_color("boxing", "G", false_start=True) == "F"


def test_physical_led_maps_false_start_and_miss_to_red():
    assert led_color("G") == "G"
    assert led_color("Y") == "Y"
    assert led_color("R") == "R"
    assert led_color("F") == "R"
    assert led_color("M") == "R"
    assert led_color(None) == "0"
    assert led_color("nonsense") == "0"


def test_edge_payload_uses_the_configured_duration(monkeypatch):
    monkeypatch.setattr(config, "LED_GLOW_MS", 123)
    assert edge_payload("F") == {"color": "F", "ms": 123}
    assert edge_payload("G", ms=40) == {"color": "G", "ms": 40}


# ----------------------------------------------------------------------
# rig handshake / LED ownership
# ----------------------------------------------------------------------
def test_ready_takes_ownership_and_caches_firmware():
    rig = FakeRig()
    rig.start()
    assert list(rig.sent)[:3] == ["DEMO 0", "PING", "VER"]
    assert rig.demo_on is False
    assert rig.ready is True
    assert rig.handshake is True
    assert rig.firmware == "reflex-fw 1.0"
    rig.stop()


def test_fake_rig_sent_log_shows_glow_then_release(monkeypatch):
    monkeypatch.setattr(config, "LED_GLOW_MS", 40)
    rig = FakeRig()
    rig.start()
    rig.sent.clear()
    rig.led_log.clear()

    rig.glow("F")
    assert rig.led_state == "R"
    assert list(rig.led_log) == ["LED R"]
    assert [line for line in rig.sent if line.startswith("LED ")] == ["LED R"]

    time.sleep(0.15)
    assert rig.led_state == "0"
    assert list(rig.led_log) == ["LED R", "LED 0"]
    assert [line for line in rig.sent if line.startswith("LED ")] == [
        "LED R", "LED 0",
    ]
    rig.stop()


def test_explicit_off_cancels_a_pending_glow(monkeypatch):
    monkeypatch.setattr(config, "LED_GLOW_MS", 120)
    rig = FakeRig()
    rig.start()
    rig.sent.clear()
    rig.glow("G")
    rig.led(None)
    time.sleep(0.2)
    leds = [line for line in rig.sent if line.startswith("LED ")]
    # The superseded timer must not append a second off after the explicit one.
    assert leds == ["LED G", "LED 0"]
    rig.stop()


def test_manual_led_command_is_a_flash_not_a_latch(monkeypatch):
    monkeypatch.setattr(config, "LED_GLOW_MS", 40)
    with tempfile.TemporaryDirectory() as tmp:
        store, player, rig, engine = _make_engine(tmp)
        engine.set_led("Y")
        assert rig.led_state == "Y"
        time.sleep(0.15)
        assert rig.led_state == "0"
        engine.set_led("0")
        assert rig.led_state == "0"
        rig.stop()


class _MockSerial:
    def __init__(self) -> None:
        self.is_open = True
        self.writes: list[str] = []
        self.flushed = 0

    def write(self, data: bytes) -> int:
        self.writes.append(data.decode("ascii"))
        return len(data)

    def flush(self) -> None:
        self.flushed += 1

    def close(self) -> None:
        self.is_open = False


def test_serial_stop_sends_disarm_and_led_off(monkeypatch):
    monkeypatch.setattr(config, "LED_GLOW_MS", 5000)
    rig = SerialRig("COM_TEST")
    rig.ser = _MockSerial()
    rig.glow("G")
    rig.stop()
    assert rig.ser.writes[0] == "LED G\n"
    assert "DISARM\n" in rig.ser.writes
    assert rig.ser.writes[-1] == "LED 0\n"
    assert rig.ser.writes.index("DISARM\n") < rig.ser.writes.index("LED 0\n")
    assert rig.ser.flushed >= 1
    assert rig.ser.is_open is False
    assert rig.led_state == "0"


def test_port_busy_is_reported_as_a_clear_status():
    assert SerialRig._is_busy_error(
        Exception("could not open port 'COM6': PermissionError(13, 'Access is denied.')")
    )
    assert SerialRig._is_busy_error(Exception("port is busy"))
    assert not SerialRig._is_busy_error(Exception("read timed out"))
    rig = SerialRig("COM_TEST")
    rig.port_busy = True
    rig.last_error = "COM_TEST is busy - close the Arduino IDE Serial Monitor"
    status = rig.status()
    assert status["port_busy"] is True
    assert "Arduino IDE" in status["last_error"]


# ----------------------------------------------------------------------
# engine result payloads
# ----------------------------------------------------------------------
def _make_engine(tmp: str, rig: FakeRig | None = None,
                 vision: VisionEngine | None = None):
    store = Store(Path(tmp) / "store.json")
    player = store.create_player("Hardware Tester")
    rig = rig or FakeRig(simulate_hits=False)
    if not rig.connected:
        rig.start()
    engine = DrillEngine(store, rig, vision or VisionEngine(),
                         ConnectionManager())
    engine.player_id = player["id"]
    engine.session_id = store.start_session(player["id"], "racing", "laptop")
    engine.rep_no = 1
    return store, player, rig, engine


def test_racing_result_carries_edge_and_led_and_releases(monkeypatch):
    monkeypatch.setattr(config, "LED_GLOW_MS", 50)

    async def run() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store, player, rig, engine = _make_engine(tmp)
            engine.t0 = time.perf_counter() - 0.2
            await engine._finish(rt_ms=220, method="vision")
            result = engine.last_result
            assert result is not None
            assert result["type"] == "result"
            # Existing racing fields stay untouched.
            assert result["rt_ms"] == 220
            assert result["grade"] == "G"
            assert result["false_start"] is False
            assert result["edge"] == {"color": "G", "ms": 50}
            assert result["led"] == "G"
            assert [line for line in rig.sent if line.startswith("LED ")] == [
                "LED G",
            ]
            time.sleep(0.15)
            assert [line for line in rig.sent if line.startswith("LED ")] == [
                "LED G", "LED 0",
            ]
            assert rig.led_state == "0"
            rig.stop()

    asyncio.run(run())


def test_racing_false_start_result_is_violet_edge_red_led(monkeypatch):
    monkeypatch.setattr(config, "LED_GLOW_MS", 40)

    async def run() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store, player, rig, engine = _make_engine(tmp)
            await engine._finish(rt_ms=None, false_start=True,
                                 method="jump start")
            result = engine.last_result
            assert result is not None
            assert result["grade"] == "F"
            assert result["false_start"] is True
            assert result["edge"] == {"color": "F", "ms": 40}
            assert result["led"] == "R"
            assert [line for line in rig.sent if line.startswith("LED ")] == [
                "LED R",
            ]
            time.sleep(0.12)
            assert rig.led_state == "0"
            rig.stop()

    asyncio.run(run())


def test_boxing_result_edge_ignores_the_rt_grade(monkeypatch):
    monkeypatch.setattr(config, "LED_GLOW_MS", 50)
    # Force a slow-looking RT grade while the action is still correct.  The
    # edge must stay green because boxing feedback is about the action.
    monkeypatch.setattr(boxing, "grade_for", lambda rt, correct: "R")

    async def run() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "store.json")
            player = store.create_player("Boxer")
            rig = FakeRig(simulate_hits=False)
            rig.start()
            vision = VisionEngine()
            engine = DrillEngine(store, rig, vision, ConnectionManager())
            engine.mode = "boxing"
            engine.stance = "orthodox"
            engine.player_id = player["id"]
            engine.session_id = store.start_session(player["id"], "boxing",
                                                   "laptop")
            engine.boxing_set = list(boxing.DEFAULT_SET)
            engine.boxing_index = 0
            engine.boxing_cue = boxing.cue_payload("jab", "orthodox")
            engine.source = "fake"
            engine.drill_clock = "backend"
            engine.t0 = time.perf_counter()
            engine.rep_no = 1
            vision.simulate_boxing("jab", engine.t0, "orthodox")

            await engine._finish_boxing_cue()
            result = engine.last_result
            assert result is not None
            assert result["mode"] == "boxing"
            assert result["correct"] is True
            assert result["grade"] == "R"          # RT grade kept for the card
            assert result["edge"] == {"color": "G", "ms": 50}
            assert result["led"] == "G"
            assert [line for line in rig.sent if line.startswith("LED ")] == [
                "LED G",
            ]
            time.sleep(0.15)
            assert rig.led_state == "0"
            rig.stop()

    asyncio.run(run())


# ----------------------------------------------------------------------
# DIST calibration
# ----------------------------------------------------------------------
def test_dist_parsing_updates_last_distance_cm():
    rig = FakeRig()
    rig.start()
    rig._emit("DIST 42")
    assert rig.last_distance_cm == 42
    rig._emit("DIST -1")
    assert rig.last_distance_cm == -1
    rig.stop()


def test_distance_is_polled_only_while_idle():
    with tempfile.TemporaryDirectory() as tmp:
        store, player, rig, engine = _make_engine(tmp)
        engine.last_dist_poll = 0.0
        engine.phase = "idle"
        rig.sent.clear()
        engine._poll_distance(time.perf_counter())
        assert "DIST" in rig.sent
        assert isinstance(rig.last_distance_cm, int)
        assert engine.snapshot()["last_distance_cm"] == rig.last_distance_cm

        # 4 Hz throttle: an immediate second call is suppressed.
        count = list(rig.sent).count("DIST")
        engine._poll_distance(time.perf_counter())
        assert list(rig.sent).count("DIST") == count

        # Never during arm/wait/response.
        engine.phase = "response"
        engine.last_dist_poll = 0.0
        rig.sent.clear()
        engine._poll_distance(time.perf_counter())
        assert "DIST" not in rig.sent
        rig.stop()


# ----------------------------------------------------------------------
# camera health + edge assets
# ----------------------------------------------------------------------
def test_camera_health_fields():
    from backend.main import camera_info

    info = camera_info()
    assert info["index"] == config.CAMERA_INDEX == 0
    assert info["width"] == 640
    assert info["height"] == 480
    assert info["fps"] == 30
    assert info["backend"] == "DSHOW"
    assert info["fourcc"] == "MJPG"


def test_health_endpoint_keeps_pose_and_adds_camera(monkeypatch):
    import backend.main as main

    monkeypatch.setattr(main, "rig", SimpleNamespace(status=lambda: {
        "backend": "fake", "connected": True, "ready": True,
    }), raising=False)
    monkeypatch.setattr(main, "vision", SimpleNamespace(status=lambda: {
        "mode": "local",
        "pose_ok": True,
        "pose_backend": "tasks",
        "pose_model_exists": True,
    }), raising=False)
    monkeypatch.setattr(main, "engine", SimpleNamespace(health=lambda: {}),
                        raising=False)
    monkeypatch.setattr(main, "manager", SimpleNamespace(snapshot=lambda: []),
                        raising=False)

    payload = asyncio.run(main.health())
    assert payload["ok"] is True
    assert payload["camera"]["backend"] == "DSHOW"
    assert payload["camera"]["index"] == 0
    # Pose Landmarker health is still there, unchanged.
    assert payload["vision"]["pose_backend"] == "tasks"
    assert payload["vision"]["pose_model_exists"] is True


def test_edge_assets_expose_the_frozen_contract():
    css = (ROOT / "frontend" / "edge.css").read_text(encoding="utf-8")
    js = (ROOT / "frontend" / "edge.js").read_text(encoding="utf-8")
    assert "#edge-glow" in css
    assert "position: fixed" in css
    assert "pointer-events: none" in css
    assert "z-index: 1100" in css
    assert "inset 0 0 140px 36px" in css
    assert "var(--dur-edge" in css
    assert "prefers-reduced-motion: reduce" in css
    assert "prefers-contrast: more" in css
    for cls in ("edge-good", "edge-warn", "edge-bad", "edge-false"):
        assert "." + cls in css
    assert "window.Split2ndEdge" in js
    assert "flash: flash" in js
    assert "clear: clear" in js
    for key in ('G: "edge-good"', 'Y: "edge-warn"', 'R: "edge-bad"',
                'F: "edge-false"'):
        assert key in js
